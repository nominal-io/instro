from curses import window

from plotly.graph_objs._figurewidget import FigureWidget

from instro.lib.publishers import FilePublisher
from .publisher  import Publisher
from ..consumers import Consumer, FileConsumer
from ..sinks import Sink, PlotlyLiveSink

import tempfile

class PublishConsumeSink():
    def __init__( self, publisher: Publisher, consumer:Consumer, sink: Sink) -> Publisher:
        '''
        A class that acts like publisher, but wraps some   publish/consume/sink combo
        '''
        self._publisher = publisher
        self._consumer  = consumer
        self._sink = sink
        self.start()

    def publish(self, data, **kwargs):
        self._publisher.publish(data, **kwargs)

    def close(self):
        [k.close() for k in [self._publisher, self._consumer, self._sink]]
        
    def start(self):
        self._sink.start(consumer=self._consumer)

class FilePublishConsume(PublishConsumeSink):
    def __init__( self, sink: Sink, poll_s: float = 0.1) ->Publisher:
        '''
        Give me a sink, and ill make it a publisher hiding the file pub/sub from you
        '''
        # create a temp file dir 
        self._tempdir = tempfile.TemporaryDirectory()
        publisher = FilePublisher(format='jsonl', directory=self._tempdir.name, custom_file_name='plotly_sinky')
        consumer  = FileConsumer(file_path=publisher.file_path, poll_s=poll_s)
        super().__init__(publisher=publisher, consumer=consumer, sink=sink )

    def close(self):
        super().close()
        self._tempdir.cleanup()

class PlotlyLivePublisher(FilePublishConsume):
    '''
    A plotlyb live sink, that looks like publisher. uses File Pub/sub by default
    '''
    def __init__(
            self, 
            window: int = 50, 
            plot_poll_s: float = 0.01,
            publisher_poll_s:float=.1,  
            fig: FigureWidget | None = None) ->Publisher:

        sink = PlotlyLiveSink(window=window, poll_s=plot_poll_s, fig=fig) 
        super().__init__(sink=sink, poll_s=publisher_poll_s)


    def display(self):
        return self._sink.display()