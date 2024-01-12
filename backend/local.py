import socket
import supr
from supr.backend import baseinstance, basebackend

class instance(baseinstance):
    private_ip_address = lambda self: self.public_ip_address
    def __init__(self, name):
        self.state = 'running'
        self.id = name
        self.name = name
        self.public_ip_address = socket.gethostbyname(name)
    def start(self): raise NotImplementedError()
    def stop(self): raise NotImplementedError()
    def terminate(self): raise NotImplementedError()
    def _snapshot(self): raise NotImplementedError()
    def _attach_volume(self, volume): raise NotImplementedError()

class backend(basebackend):
    def get_instance_by_id(self, _id) -> instance:
        return supr.instance(_id)
    def get_instances(self, *filters) -> list[instance]:
        return [supr.instance(name) for name in supr.CONF['local']]
    def create_instance(self, name) -> instance:
        raise NotImplementedError()

