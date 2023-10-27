import os, sys, time
import threading
from datetime import timedelta
from abc import ABCMeta, abstractmethod
from pathlib import PurePath as path
import fabric, paramiko
from patchwork.transfers import rsync
from patchwork.files import exists
from paramiko import ssh_exception
from supr import CONF, ANSI, LOCAL_EXCLUDE, state
from supr import log

class F:
    def __init__(self, name): self.name = name
    def __getitem__(self, values):
        # TODO abstract this; anything aws should be in aws.py
        return {'Name': self.name, 'Values': [values, ] if isinstance(values, str) else list(values) }

F_ID = F('instance-id')
F_STATE = F('instance-state-name')
F_ACTIVE = F_STATE['pending', 'running', 'stopping', 'stopped']
F_NAME = F('tag:Name')

_CARRIAGE_RETURN = b'^\x00\x00\x00\x00\x00\x00\x00\x01\n'
class sshtransport(paramiko.transport.Transport):
    def _send_user_message(self, data):
        if hasattr(self, '_activity_callback') and bytes(data) == _CARRIAGE_RETURN:
            threading.Thread(target=self._activity_callback).start()
        return super()._send_user_message(data)
class sshclient(paramiko.SSHClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.activity_callback = lambda: None
    def connect(self, *args, **kwargs):
        kwargs['transport_factory'] = self.get_transport_factory()
        super().connect(*args, **kwargs)
    def get_transport_factory(self):
        def f(*args, **kwargs):
            t = sshtransport(*args, **kwargs)
            t._activity_callback = self.activity_callback
            return t
        return f
class connection(fabric.Connection):
    def __init__(self, *args, **kwargs):
        activity_callback = kwargs.pop('activity_callback', lambda: None)
        super().__init__(*args, **kwargs)
        self.client = sshclient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.activity_callback = activity_callback

class _instance:
    conf = property(lambda self: self.__class__.get_conf(self.name))
    is_super = property(lambda self: self.conf.get('super', False))
    user = property(lambda self: self.conf['user'])
    group = property(lambda self: self.conf.get('group', self.user))
    #uid = property(lambda self: self.cmd(f"id -u {self.user}", hide=True).stdout.strip())
    #gid = property(lambda self: self.cmd(f"id -g {self.group}", hide=True).stdout.strip())
    home = property(lambda self: path("/home")/self.conf['user'])
    env = property(lambda self: path(self.conf['env']))
    py_bin = property(lambda self: self.env/'bin/python')
    pip_bin = property(lambda self: self.env/'bin/pip')
    dist_release = property(lambda self: self.conf['dist_release'])
    apt_cache = property(lambda self: path(self.conf['apt_cache']) if 'apt_cache' in self.conf else None)
    wheel_cache = property(lambda self: path(self.conf['wheel_cache']) if 'wheel_cache' in self.conf else None)
    _storage_handlers = [
        ('native', lambda self, name: self.mount(name)),
    ]
    @classmethod
    def new(cls, name):
        from supr import backend
        i = backend.create_instance(name)
        state.change(i.id, 'start')
        i.wait_until_running()
        while True:
            try: i.connection.open(); break
            except ssh_exception.NoValidConnectionsError: time.sleep(2)
        i.cmd('sudo hostname %s' % i.name)
        i.connection.local(f"ssh-keyscan {i.private_ip_address} >> ~/.ssh/known_hosts")
        i.install_essential()
        i.attach_volumes()
        i.install_base()
        i.install_crontab()
        return i
    def __repr__(self) -> str:
        return "".join([
            "{%s}{state}{RST} " % ("GRN" if self.state == 'running' else "RED"), 
            "{PNK}{BLD}{s}{name}{RST} ", 
            "{ITL}{instance_type}{RST} ", 
            "{UDL}{public_ip_address}{RST} {UDL}{private_ip_address}{RST} ", 
            #"{DIM}tags={tags}{RST}", 
        ]).format(**dict(
            state=self.state, 
            s="*" if self.is_super else "",
            name=self.name, 
            instance_type=self.instance_type, 
            public_ip_address=self.public_ip_address, 
            private_ip_address=self.private_ip_address, 
            tags=self.tags, 
        ), **ANSI)
    @staticmethod
    def get_conf(name):
        assert {'user', 'env', 'key_pair'}.issubset(CONF[name])
        assert 'file' in CONF[name]['key_pair']
        return CONF[name]
    @property
    def connection(self) -> connection:
        if not hasattr(self, '_connection'):
            self._connection = connection(
                self.private_ip_address, 
                user=self.user, 
                connect_kwargs=dict(key_filename=self.conf['key_pair']['file']), 
                inline_ssh_env=True, 
                activity_callback=state.activity_callback(self.id))
        return self._connection
    @property
    def vars(self):
        return self.conf.get('vars', {})
    def print(self):
        print(self)
        return self
    def start(self):
        state.change(self.id, 'start')
        self._start()
        self.wait_until_running()
    def stop(self):
        self._stop()
        self.wait_until_stopped()
        state.change(self.id, 'stop')
    def terminate(self):
        if self.is_super: print("you probably don't want to do that"); return
        self._terminate()
        self.wait_until_terminated()
        state.change(self.id, 'stop')
    def run(self, cmd, *args, **kwargs):
        state.activity(self.id)
        return self.connection.run(cmd, env=self.vars, *args, **kwargs)
    def ssh(self, cmd='bash'):
        # source {self.env}/bin/activate
        #return self.run("TERM='xterm-256color'; {cmd}", pty=True)
        return self.run(f"source {self.env}/bin/activate; {cmd}", pty=True)
    def cmd(self, *args, **kwargs):
        return self.run(' '.join(args), **kwargs)
    def put(self, local, remote):
        state.activity(self.id)
        return self.connection.put(local, remote)
    def exists(self, path):
        state.activity(self.id)
        return exists(self.connection, path)
    def rsync(self, *args, **kwargs):
        state.activity(self.id)
        #kwargs['-e'] = "ssh -o StrictHostKeyChecking=no"
        return rsync(self.connection, *args, **kwargs)
    def _attach_volume(self, name):
        state.activity(self.id)
        assert name in self.conf.get('volumes', {})
        p = self.conf['volumes'][name].get('provider', 'native')
        [v(self, name) for k, v in self._storage_handlers if k == p]
    def attach_volumes(self):
        state.activity(self.id)
        [self._attach_volume(v) for v in self.conf.get('volumes', {}).keys()]
    def mount(self, name):
        assert name in self.conf.get('volumes', ())
        conf = self.conf['volumes'][name]
        assert {'dev', 'mount'}.issubset(conf)
        self.cmd(f"sudo mkdir -p {conf['mount']}")
        self.cmd(f"sudo chown {self.user}:{self.group} {conf['mount']}")
        if self.cmd("mount | grep %s" % conf['mount'], warn=True).failed:
            self.cmd("mount%s %s %s" % (
                " -o %s" % ','.join(conf['options']) if 'options' in conf else '', 
                conf['dev'], conf['mount']))
    def install_crontab(self):
        state.activity(self.meta.id)
        for cmd in self.conf.get('crontab', ()):
            # TODO replace updated commands
            if self.cmd("crontab -l | grep %s" % cmd.split(' ')[-1], warn=True).failed:
                self.cmd(f"(crontab -l; echo '{cmd}') | crontab -")
    def _install_sh(self, cmd):
        self.cmd(f"sudo sh -c '{cmd}'")
    def _add_apt_sources(self):
        if not 'apt_sources' in self.conf: return
        assert self.dist_release
        conf = (
            f"Types: deb deb-src\n"
            f"URIs: mirror+file:///etc/apt/mirrors/debian.list\n"
            f"Suites: {self.dist_release}\n"
            f"Components: ")
        conf += ' '.join(self.conf['apt_sources'])
        self.cmd(f"echo '{conf}' | sudo tee /etc/apt/sources.list.d/user.sources")        
    def _install_apt(self, name):
        if self.apt_cache and self.exists(self.apt_cache):
            self.cmd(f"sudo apt-get install -y -o Dir::Cache::Archives={self.apt_cache} {name}")
        else:
            self.cmd(f"sudo apt-get install -y {name}")
    def _install_pip(self, name):
        # TODO pyopencl builds itself every time
        if self.wheel_cache and self.exists(self.wheel_cache):
            self.cmd(f"{self.pip_bin} wheel --wheel-dir={self.wheel_cache} {name}")
            self.cmd(f"{self.pip_bin} install --no-index --find-links={self.wheel_cache} {name}")
        else:
            self.cmd(f"{self.pip_bin} install --upgrade {name}")
    def _install_github(self, user_repo):
        user, repo = user_repo.split('/')
        self.cmd(f"git -C {repo} pull || git clone --depth=1 https://github.com/{user}/{repo}.git {repo}")
        self.cmd(f"{self.pip_bin} install {repo}")
    def _install_local(self, name):
        self.rsync(name, '', delete=False, exclude=LOCAL_EXCLUDE)
        # TODO: this could support more options
        self.cmd(f"{self.pip_bin} install -e {name}")
    def _install(self, source, name):
        match source, name:
            case ['apt', name]: self._install_apt(name)
            case ['pip', name]: self._install_pip(name)
            case ['github', name]: self._install_github(name)
            case ['local', name]: self._install_local(name)
            case ['sh', cmd]: self._install_sh(cmd)
            case _: assert False, f"invalid package source {source}"
    def install_essential(self):
        # TODO: strip this down even more so we can get s3 mounted and have a cache available
        # `upgrade` might be excessive
        self._add_apt_sources()
        self.cmd("sudo apt-get update && sudo apt-get upgrade -y")
        self.cmd("sudo apt-get install -y linux-headers-$(uname -r)")
        self._install_apt('rsync s3fs git-core python3 python3-pip python3-venv')
        if not self.exists(self.env):
            self.cmd(f"python3 -m venv {self.env}")
        self._install_pip('setuptools wheel')
    def install_base(self):
        state.activity(self.id)
        for s_n in self.conf.get('packages', {}).get('base', ()):
            self._install(*s_n.split(':'))
    def deploy(self, no_deps=False):
        state.activity(self.id)
        for s_n in self.conf.get('packages', {}).get('app', ()):
            if no_deps and not s_n.startswith('local:'): continue
            self._install(*s_n.split(':'))
        if 'entrypoint' in self.conf:
            self.ssh(self.conf['entrypoint'])

class _backend:
    def get_instance(self, name, *filters) -> _instance:
        i = self.get_instances(*list(filters) + [F_NAME[name]])
        if len(i) < 1: print("no instances with name %s" % name); return None
        if len(i) > 1: print("warning: multiple instances with name %s" % name)
        return i[0]

class _abstractinstance(metaclass=ABCMeta):
    id = property()
    name = property()
    state = property()
    tags = property()
    public_ip_address = property()
    private_ip_address = property()
    instance_type = property()
    def _start(self) -> None: pass
    def _stop(self) -> None: pass
    def _terminate(self) -> None: pass
    def wait_until_running(self) -> None: pass
    def wait_until_stopped(self) -> None: pass
    def wait_until_terminated(self) -> None: pass
    def _attach_volume(self, name) -> None: pass

class _abstractbackend(metaclass=ABCMeta):
    def create_instance(self, name): pass
    def get_instance_by_id(self, _id): pass
    def get_instances(self, *filters): pass

class baseinstance(_instance, _abstractinstance): pass
class basebackend(_backend, _abstractbackend): pass

