//! Poll-loop health metrics for the OPC-UA streaming session.
//!
//! Accumulates per-iteration read statistics over a fixed window and emits a
//! single aggregated `tracing::debug!` line every
//! [`POLL_LOOP_METRICS_FLUSH_INTERVAL`] so log volume stays bounded regardless
//! of polling frequency.
//!
//! Typical usage from the poll loop: construct an empty window with
//! [`PollLoopMetricsLogger::new`], fold each read in with
//! [`PollLoopMetricsLogger::record_read`]. The logger will automatically flush the window
//! when the specified flush interval elapses according to the system clock.

use std::time::Duration;

use tokio::time::Instant;

#[derive(Debug, Clone, Copy)]
/// Counts of successful and failed reads.
pub struct NodeReadCounts {
    /// Number of successful reads.
    pub valid_samples: u64,
    /// Number of failed reads.
    pub invalid_samples: u64,
}

/// A trait for types that can anchor the start of a read operation.
pub trait ReadAnchor: 'static {
    /// Get the duration since the start of the read operation.
    fn elapsed(self, now: Instant) -> Duration;
}

impl ReadAnchor for Instant {
    fn elapsed(self, now: Instant) -> Duration {
        now.duration_since(self)
    }
}

/// Rolling counters and timing aggregates for a single poll-loop metrics window.
/// Meant for developer-facing metrics logging only.
/// Eventually, this may end up integrating with our production telemetry system, whatever that winds up being.
#[derive(Debug)]
pub struct PollLoopMetricsLogger {
    /// Interval between the start of the current window and the start of the next window.
    flush_interval: Duration,
    /// Current window.
    window: MetricsWindow,
}

#[derive(Debug)]
struct MetricsWindow {
    /// Monotonic start of the current aggregation window, set at construction
    /// time by [`PollLoopMetrics::new`].
    pub window_start: Instant,
    /// Count of poll-loop iterations observed in the current window (both successful and failed reads).
    pub iterations: u64,
    /// Number of `read_many_attributes` calls that returned `Ok` in the window.
    pub ok_count: u64,
    /// Number of `read_many_attributes` calls that returned `Err` in the window.
    pub err_count: u64,
    /// Sum of wall-clock durations of every `read_many_attributes` call in the window;
    /// used to compute the per-call average at flush time.
    pub read_duration_sum: Duration,
    /// Minimum observed read duration in the window.
    pub read_duration_min: Option<Duration>,
    /// Maximum observed read duration in the window.
    pub read_duration_max: Option<Duration>,
    /// Total number of samples successfully decoded from `Ok` reads and forwarded
    /// via the `on_data` callback.
    pub valid_samples: u64,
    /// Total number of raw values from `Ok` reads that failed to decode into an
    /// `OpcUaDataPoint` and were dropped.
    pub invalid_samples: u64,
}

impl MetricsWindow {
    fn new() -> Self {
        Self {
            window_start: Instant::now(),
            iterations: 0,
            ok_count: 0,
            err_count: 0,
            read_duration_sum: Duration::ZERO,
            read_duration_min: None,
            read_duration_max: None,
            valid_samples: 0,
            invalid_samples: 0,
        }
    }

    /// Folds a read's duration and outcome into the current window.
    ///
    /// Pass `Some(counts)` when the underlying `read_many_attributes` call
    /// succeeded (counts are then accumulated into `valid_samples` /
    /// `invalid_samples`); pass `None` when the read itself errored.
    fn update(&mut self, read_duration: Duration, counts: Option<NodeReadCounts>) {
        self.iterations = self.iterations.saturating_add(1);
        self.read_duration_sum = self.read_duration_sum.saturating_add(read_duration);

        if let Some(min) = self.read_duration_min {
            self.read_duration_min = Some(min.min(read_duration));
        } else {
            self.read_duration_min = Some(read_duration);
        }

        if let Some(max) = self.read_duration_max {
            self.read_duration_max = Some(max.max(read_duration));
        } else {
            self.read_duration_max = Some(read_duration);
        }

        match counts {
            Some(NodeReadCounts {
                valid_samples,
                invalid_samples,
            }) => {
                self.ok_count = self.ok_count.saturating_add(1);
                self.valid_samples = self.valid_samples.saturating_add(valid_samples);
                self.invalid_samples = self.invalid_samples.saturating_add(invalid_samples);
            }

            None => {
                self.err_count = self.err_count.saturating_add(1);
            }
        }
    }
}

impl PollLoopMetricsLogger {
    #[must_use]
    /// Creates a new metrics window.
    pub fn new(flush_interval: Duration) -> Self {
        Self {
            flush_interval,
            window: MetricsWindow::new(),
        }
    }

    /// Anchor the start of a read operation.
    ///
    /// Returns an anchor passed to [`PollLoopMetricsLogger::record_read`] to record the read's duration and outcome.
    #[must_use]
    pub fn start_read(&self) -> impl ReadAnchor {
        Instant::now()
    }

    /// Folds a read's duration and outcome into the current window.
    ///
    /// Pass `Some(counts)` when the underlying `read_many_attributes` call
    /// succeeded (counts are then accumulated into `valid_samples` /
    /// `invalid_samples`); pass `None` when the read itself errored.
    pub fn finish_read(&mut self, anchor: impl ReadAnchor, counts: Option<NodeReadCounts>) {
        let duration = anchor.elapsed(Instant::now());
        self.window.update(duration, counts);

        let now = Instant::now();
        if now.duration_since(self.window.window_start) >= self.flush_interval {
            self.flush(now);
            self.window = MetricsWindow::new()
        }
    }

    /// Emits the aggregated `poll_loop` debug log line for the current window.
    ///
    /// `now` is used only to compute the window's elapsed wall-clock duration
    /// for the `loop_rate_hz` field; the window itself is not reset.
    fn flush(&self, now: Instant) {
        if self.window.iterations < 1 {
            return;
        }

        let window_secs = now.duration_since(self.window.window_start).as_secs_f64();

        let loop_rate_hz = if window_secs > 0.0 {
            self.window.iterations as f64 / window_secs
        } else {
            0.0
        };

        let read_total_count = self.window.ok_count.saturating_add(self.window.err_count);

        let read_avg_ms = if read_total_count > 0 {
            self.window.read_duration_sum.as_secs_f64() * 1000.0 / read_total_count as f64
        } else {
            0.0
        };

        tracing::debug!(
            target: "opcua::client::poll_loop",
            valid_samples = self.window.valid_samples,
            invalid_samples = self.window.invalid_samples,
            successful_read_count = self.window.ok_count,
            failed_read_count = self.window.err_count,
            loop_rate_hz,
            read_avg_ms,
        );
    }
}

impl Drop for PollLoopMetricsLogger {
    fn drop(&mut self) {
        tracing::info!(target: "opcua::client::poll_loop", "final metrics flush");
        self.flush(Instant::now());
    }
}

#[cfg(test)]
mod tests {
    use tokio::time::Duration;
    use tokio::time::Instant;

    use super::NodeReadCounts;
    use super::PollLoopMetricsLogger;
    use super::ReadAnchor;

    /// A mock anchor for deterministic timestamp testing.
    #[derive(Clone, Copy, Debug)]
    struct MockAnchor {
        pub start: Instant,
        pub end: Instant,
    }

    impl ReadAnchor for MockAnchor {
        fn elapsed(self, _now: Instant) -> Duration {
            self.end.duration_since(self.start)
        }
    }

    impl MockAnchor {
        fn new(start_offset: Duration, dur: Duration) -> MockAnchor {
            let base = Instant::now();
            #[expect(clippy::arithmetic_side_effects, reason = "test code")]
            MockAnchor {
                start: base + start_offset,
                end: base + start_offset + dur,
            }
        }
    }

    #[test]
    fn from_first_read_ok_seeds_window_with_that_read() {
        let anchor = MockAnchor::new(Duration::ZERO, Duration::from_millis(10));
        let mut m = PollLoopMetricsLogger::new(Duration::from_secs(5));
        m.finish_read(
            anchor,
            Some(NodeReadCounts {
                valid_samples: 3,
                invalid_samples: 1,
            }),
        );

        assert_eq!(m.window.iterations, 1);
        assert_eq!(m.window.ok_count, 1);
        assert_eq!(m.window.err_count, 0);
        assert_eq!(m.window.valid_samples, 3);
        assert_eq!(m.window.invalid_samples, 1);
        assert_eq!(m.window.read_duration_sum, Duration::from_millis(10));
        assert_eq!(m.window.read_duration_min, Some(Duration::from_millis(10)));
        assert_eq!(m.window.read_duration_max, Some(Duration::from_millis(10)));
    }

    #[test]
    fn from_first_read_err_seeds_err_counter() {
        let anchor = MockAnchor::new(Duration::ZERO, Duration::from_millis(7));
        let mut m = PollLoopMetricsLogger::new(Duration::from_secs(5));
        m.finish_read(anchor, None);

        assert_eq!(m.window.iterations, 1);
        assert_eq!(m.window.ok_count, 0);
        assert_eq!(m.window.err_count, 1);
        assert_eq!(m.window.valid_samples, 0);
        assert_eq!(m.window.invalid_samples, 0);
        assert_eq!(m.window.read_duration_sum, Duration::from_millis(7));
        assert_eq!(m.window.read_duration_min, Some(Duration::from_millis(7)));
        assert_eq!(m.window.read_duration_max, Some(Duration::from_millis(7)));
    }

    #[test]
    fn record_read_ok_accumulates_counts_and_tracks_min_max_sum() {
        let a1 = MockAnchor::new(Duration::from_secs(1), Duration::from_millis(10));
        let a2 = MockAnchor::new(Duration::from_secs(2), Duration::from_millis(15));
        let a3 = MockAnchor::new(Duration::from_secs(3), Duration::from_millis(8));

        let mut m = PollLoopMetricsLogger::new(Duration::from_secs(5));

        m.finish_read(
            a1,
            Some(NodeReadCounts {
                valid_samples: 3,
                invalid_samples: 1,
            }),
        );

        m.finish_read(
            a2,
            Some(NodeReadCounts {
                valid_samples: 2,
                invalid_samples: 0,
            }),
        );
        m.finish_read(
            a3,
            Some(NodeReadCounts {
                valid_samples: 4,
                invalid_samples: 2,
            }),
        );

        assert_eq!(m.window.iterations, 3);
        assert_eq!(m.window.ok_count, 3);
        assert_eq!(m.window.err_count, 0);
        assert_eq!(m.window.valid_samples, 9);
        assert_eq!(m.window.invalid_samples, 3);
        // Confirm min = 8ms, max = 15ms, sum = 33ms
        assert_eq!(m.window.read_duration_min, Some(Duration::from_millis(8)));
        assert_eq!(m.window.read_duration_max, Some(Duration::from_millis(15)));
        assert_eq!(m.window.read_duration_sum, Duration::from_millis(33));
    }

    #[test]
    fn record_read_err_increments_err_count_and_iteration() {
        let a1 = MockAnchor::new(Duration::from_secs(1), Duration::from_millis(15));
        let a2 = MockAnchor::new(Duration::from_secs(2), Duration::from_millis(25));
        let mut m = PollLoopMetricsLogger::new(Duration::from_secs(5));
        m.finish_read(a1, None);
        m.finish_read(a2, None);

        assert_eq!(m.window.iterations, 2);
        assert_eq!(m.window.ok_count, 0);
        assert_eq!(m.window.err_count, 2);
        assert_eq!(m.window.valid_samples, 0);
        assert_eq!(m.window.invalid_samples, 0);
        assert_eq!(m.window.read_duration_sum, Duration::from_millis(40));
        assert_eq!(m.window.read_duration_min, Some(Duration::from_millis(15)));
        assert_eq!(m.window.read_duration_max, Some(Duration::from_millis(25)));
    }

    #[test]
    fn record_read_mixed_ok_and_err() {
        let a1 = MockAnchor::new(Duration::from_secs(1), Duration::from_millis(5));
        let a2 = MockAnchor::new(Duration::from_secs(2), Duration::from_millis(50));

        let mut m = PollLoopMetricsLogger::new(Duration::from_secs(5));

        m.finish_read(
            a1,
            Some(NodeReadCounts {
                valid_samples: 1,
                invalid_samples: 0,
            }),
        );

        m.finish_read(a2, None);

        assert_eq!(m.window.iterations, 2);
        assert_eq!(m.window.ok_count, 1);
        assert_eq!(m.window.err_count, 1);
        assert_eq!(m.window.valid_samples, 1);
        assert_eq!(m.window.invalid_samples, 0);
        assert_eq!(m.window.read_duration_sum, Duration::from_millis(55));
        assert_eq!(m.window.read_duration_min, Some(Duration::from_millis(5)));
        assert_eq!(m.window.read_duration_max, Some(Duration::from_millis(50)));
    }

    #[test]
    fn auto_flush_resets_window() {
        let anchor = MockAnchor::new(Duration::ZERO, Duration::from_millis(1));
        let mut m = PollLoopMetricsLogger::new(Duration::ZERO);
        m.finish_read(
            anchor,
            Some(NodeReadCounts {
                valid_samples: 1,
                invalid_samples: 0,
            }),
        );

        assert_eq!(m.window.iterations, 0);
        assert_eq!(m.window.ok_count, 0);
        assert_eq!(m.window.err_count, 0);
        assert_eq!(m.window.valid_samples, 0);
        assert_eq!(m.window.invalid_samples, 0);
        assert_eq!(m.window.read_duration_sum, Duration::ZERO);
        assert_eq!(m.window.read_duration_min, None);
        assert_eq!(m.window.read_duration_max, None);
    }

    #[test]
    fn flush_after_only_errors_does_not_panic() {
        // read_ok_count == 0, so the read_avg_ms branch must take the zero fallback.
        let anchor = MockAnchor::new(Duration::ZERO, Duration::from_millis(10));
        let mut m = PollLoopMetricsLogger::new(Duration::from_secs(5));
        m.finish_read(anchor, None);
        let flush_at = m.window.window_start + Duration::from_secs(5);
        m.flush(flush_at);
    }

    #[test]
    fn instant_now_is_monotonic_relative_to_window_start() {
        // Guards against accidentally using a clock that can run backwards.
        let anchor = MockAnchor::new(Duration::ZERO, Duration::from_millis(0));
        let mut m = PollLoopMetricsLogger::new(Duration::from_secs(5));
        m.finish_read(anchor, None);
        let later = MockAnchor::new(Duration::from_secs(1), Duration::from_millis(0));
        assert!(later.start >= m.window.window_start);
    }
}
