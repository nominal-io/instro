from instro.lib.types import Measurement,Command
import abc 
class Publisher(abc.ABC):
    @abc.abstractmethod
    def publish(self, data: Measurement | Command, **kwargs) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...