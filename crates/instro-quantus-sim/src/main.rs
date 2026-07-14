use instro_quantus_sim::config::SimConfig;
use instro_quantus_sim::rest::SimServer;

fn main() {
    let Some(config_path) = std::env::args().nth(1) else {
        eprintln!("usage: instro-quantus-sim <rack-config.toml>");
        std::process::exit(2);
    };
    let raw = match std::fs::read_to_string(&config_path) {
        Ok(raw) => raw,
        Err(e) => {
            eprintln!("instro-quantus-sim: cannot read {config_path}: {e}");
            std::process::exit(1);
        }
    };
    let config: SimConfig = match toml::from_str(&raw) {
        Ok(config) => config,
        Err(e) => {
            eprintln!("instro-quantus-sim: invalid config: {e}");
            std::process::exit(1);
        }
    };

    match SimServer::start(config) {
        Ok(server) => {
            println!(
                "instro-quantus-sim: REST on http://127.0.0.1:{}, stream on tcp://127.0.0.1:{}",
                server.rest_port(),
                server.stream_port()
            );
            loop {
                std::thread::park();
            }
        }
        Err(e) => {
            eprintln!("instro-quantus-sim: {e}");
            std::process::exit(1);
        }
    }
}
