import time
import threading
import abc
from pathlib import PurePath as path
import uuid
import fabric, paramiko
from patchwork.transfers import rsync
from patchwork.files import exists
from supr import CONF, ANSI, state, log

class F:
    """
    Instance list filter generator.
    """
    def __init__(self, name): self.name = name
    def __getitem__(self, values):
        # TODO abstract this; anything aws should be in aws.py
        return {
            'Name': self.name, 
            'Values': [values, ] if isinstance(values, str) else list(values), 
        }

F_ID = F('instance-id')
F_STATE = F('instance-state-name')
F_ACTIVE = F_STATE['pending', 'running', 'stopping', 'stopped']
F_NAME = F('tag:Name')

class sshclient(paramiko.SSHClient):
    """
    Hacks paramiko to provide a callback for activity.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.activity_callback = lambda: None # override this
    def connect(self, *args, **kwargs):
        kwargs['transport_factory'] = self._get_transport_factory()
        super().connect(*args, **kwargs)
    def _get_transport_factory(self):
        def wrap(original, callback):
            last_call = time.time()
            def _wrap(*args, **kwargs):
                nonlocal last_call
                if time.time() - last_call > 1:
                    threading.Thread(target=callback).start()
                    last_call = time.time()
                return original(*args, **kwargs)
            return _wrap
        def factory(*args, **kwargs):
            from paramiko.common import MSG_CHANNEL_DATA
            transport = paramiko.transport.Transport(*args, **kwargs)
            _feed = transport._channel_handler_table[MSG_CHANNEL_DATA]
            transport._channel_handler_table[MSG_CHANNEL_DATA] = wrap(_feed, self.activity_callback)
            return transport
        return factory
class connection(fabric.Connection):
    """
    Extends fabric.Connection to provide a callback for activity.
    Pass your callback as `activity_callback` to the constructor.
    """
    def __init__(self, *args, **kwargs):
        activity_callback = kwargs.pop('activity_callback', lambda: None)
        super().__init__(*args, **kwargs)
        self.client = sshclient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.activity_callback = activity_callback

class _abstractinstance(metaclass=abc.ABCMeta):
    """
    Methods that must be provided on instance implementatons.
    """
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

class _instance:
    """
    Shared logic for instance implementations.
    """
    conf = property(lambda self: CONF.get(self.name, {}))
    is_super = property(lambda self: self.conf.get('super', False))
    user = property(lambda self: self.conf['user'])
    group = property(lambda self: self.conf.get('group', self.user))
    home = property(lambda self: path("/home")/self.conf['user'])
    env = property(lambda self: path(self.conf['env']))
    py_bin = property(lambda self: self.env/'bin/python')
    pip_bin = property(lambda self: self.env/'bin/pip')
    dist_release = property(lambda self: self.conf['dist_release'])
    apt_cache = property(lambda self: path(self.conf['apt_cache']) if 'apt_cache' in self.conf else None)
    wheel_cache = property(lambda self: path(self.conf['wheel_cache']) if 'wheel_cache' in self.conf else None)
    _storage_handlers = [
        ('native', lambda self, name: self.mount(name)), 
        ('swap', lambda self, name: self.swap(name))
    ]
    @classmethod
    def new(cls, name):
        from supr import backend
        i = backend.create_instance(name)
        state.change(i.id, 'start')
        print(f"creating {i.name}...")
        i.wait_until_running()
        while True:
            try: i.connection.open(); break
            except paramiko.ssh_exception.NoValidConnectionsError: time.sleep(2)
        return i
    def __repr__(self) -> str:
        return "".join([
            "{%s}{state}{RST} " % ("GRN" if self.state == 'running' else "RED"), 
            "{PNK}{BLD}{s}{name}{RST} ", 
            "{ITL}{instance_type}{RST} ", 
            "{UDL}{public_ip_address}{RST} {UDL}{private_ip_address}{RST} ", 
            #"{DIM}tags={tags}{RST}", 
        ]).format(**dict(
            state=self.state or '???', 
            s="*" if self.is_super else "",
            name=self.name or '???', 
            instance_type=self.instance_type or '???', 
            public_ip_address=self.public_ip_address or '???', 
            private_ip_address=self.private_ip_address or '???', 
            tags=self.tags or '???', 
        ), **ANSI)
    @staticmethod
    def get_conf(name):
        return CONF[name]
    @property
    def connection(self) -> connection:
        assert {'user', 'env', 'key_pair'}.issubset(self.conf), f"missing user, env, key_pair in config"
        assert 'file' in self.conf['key_pair'], f"missing key_pair.file in config"
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
    def start(self):
        state.change(self.id, 'start')
        self._start()
        self.wait_until_running()
    def stop(self):
        self._stop()
        self.wait_until_stopped()
        state.change(self.id, 'stop')
    def init(self):
        self.cmd('sudo hostname %s' % self.name)
        self.connection.local(f"ssh-keyscan {self.private_ip_address} >> ~/.ssh/known_hosts")
        self.install_essential()
        self.attach_volumes()
        self.install_base()
        self.install_crontab()
    def snapshot(self):
        id_ = self._snapshot(f"{self.name}-snapshot-{uuid.uuid4().hex}")
        print(f"{self.name}: {id_}")
    def terminate(self):
        if self.is_super: print("you probably don't want to do that"); return
        self._terminate()
        self.wait_until_terminated()
        state.change(self.id, 'stop')
    def run(self, cmd, *args, **kwargs):
        state.activity(self.id)
        return self.connection.run(cmd, env=self.vars, *args, **kwargs)
    cmd = run
    def ssh(self, shell='bash'):
        return self.run(f"source {self.env}/bin/activate; {shell}", pty=True)
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
    def mount(self, name, force=False):
        assert name in self.conf.get('volumes', ())
        conf = self.conf['volumes'][name]
        assert {'dev', 'mount'}.issubset(conf)
        dev, mount = conf['dev'], conf['mount']
        if not force and self.cmd(f"mount | grep {mount}", warn=True).ok:
            return
        self.cmd(f"sudo umount {mount}", warn=True)
        self.cmd(f"sudo mkdir -p {mount}")
        self.cmd(f"sudo chown {self.user}:{self.group} {mount}")
        if 'options' in conf:
            opts = " -o %s" % ','.join(conf['options'])
        self.cmd(f"mount{opts} {dev} {mount}")
    def swap(self, name):
        assert name in self.conf.get('volumes', ())
        conf = self.conf['volumes'][name]
        assert {'path', 'size'}.issubset(conf)
        path, size = conf['path'], conf['size']
        if not self.exists(path):
            self.cmd(f"sudo dd if=/dev/zero of={path} bs=1M count={size}")
            self.cmd(f"sudo chmod 600 {path}")
            self.cmd(f"sudo /sbin/mkswap {path}")
        self.cmd(f"sudo /sbin/swapon {path}", warn=True)
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
    def _apt_get(self, command, name=''):
        # Throwing the kitchen sink at this to get openssh-server to go non-interactive
        opts = (
            "Dpkg::Options::=\"--force-confdef\"", 
            "Dpkg::Options::=\"--force-confold\"", )
        if self.apt_cache and self.exists(self.apt_cache):
            opts += (f"Dir::Cache::Archives={self.apt_cache}", )
        args = ' '.join(f"-o {o}" for o in opts)
        self.cmd(f"sudo -E apt-get {command} -yq {args} {name}")
        #self.cmd(f"bin/apt-get-install {name}")
    def _install_apt(self, name):
        self._apt_get('install', name)
    def _install_pip(self, name):
        # TODO pyopencl builds itself every time
        # TODO --upgrade everything?
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
        self.rsync(name, '', delete=False, exclude=CONF['LOCAL_EXCLUDE'])
        # TODO: this could support more options
        self.cmd(f"{self.pip_bin} install -e {name}")
    def install(self, source, name):
        match source, name:
            case ['apt', name]: self._install_apt(name)
            case ['pip', name]: self._install_pip(name)
            case ['github', name]: self._install_github(name)
            case ['local', name]: self._install_local(name)
            case _: assert False, f"invalid package source {source}"
    def install_essential(self):
        self._add_apt_sources()
        self.cmd("sudo apt-get update")
        self._install_apt(' '.join(CONF['APT_ESSENTIAL']))
        if not self.exists(self.env):
            self.cmd(f"python3 -m venv {self.env}")
        self._install_pip(' '.join(CONF['PIP_ESSENTIAL']))
    def install_base(self):
        state.activity(self.id)
        for s_n in self.conf.get('packages', {}).get('base', ()):
            self.install(*s_n.split(':'))
    def deploy(self):
        state.activity(self.id)
        for s_n in self.conf.get('packages', {}).get('app', ()):
            self.install(*s_n.split(':'))
    def hook(self, cmd, *args):
        cmds = self.conf.get('hooks', {})
        self.start()
        self.attach_volumes()
        self.deploy()
        self.ssh(f"{cmds[cmd]} {' '.join(args)}")

class baseinstance(_instance, _abstractinstance): pass

class _abstractbackend(metaclass=abc.ABCMeta):
    """
    Methods that must be provided on backend implementatons.
    """
    def create_instance(self, name): pass
    def get_instance_by_id(self, _id): pass
    def get_instances(self, *filters): pass

class _backend:
    """
    Shared logic for backend implementations.
    """
    def get_instance(self, name, *filters) -> _instance:
        i = self.get_instances(*list(filters) + [F_NAME[name]])
        if len(i) < 1: print("no instances with name %s" % name); return None
        if len(i) > 1: print("warning: multiple instances with name %s" % name)
        return i[0]

class basebackend(_backend, _abstractbackend): pass
