import os, sys
from datetime import timedelta
import logging
import yaml

DEBUG = os.environ.get('DEBUG', False)
DIR = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.environ.get('SUPR_CONF', 'supr.yaml')
DB_PATH = os.environ.get('SUPR_DB', '.supr.db')
LOG_PATH = os.environ.get('SUPR_LOG', '.supr.log')
CONF_DEFAULTS = {
    # `stop` idle hosts after a period of activity. default: disabled
    'IDLE_TIMEOUT': None, 
    # rsync exlcude patterns used when installing with `local:`
    'LOCAL_EXCLUDE': ('.env', '.git', '.github'), 
    # packages needed to support supr features
    'APT_ESSENTIAL': ('rsync', 's3fs', 'git-core', 'python3', 'python3-pip', 'python3-venv'), 
    'PIP_ESSENTIAL': ('wheel', 'setuptools'), 
}
ANSI = dict([
    (v, "\033[%sm" % i) for v, i in \
        list(zip(['RST', 'BLD', 'DIM', 'ITL', 'UDL', 'SBL', 'FBL', 'REV'], range(8))) + \
        list(zip(['BLK', 'RED', 'GRN', 'YEL', 'BLU', 'PNK', 'CYN', 'WHT'], range(30, 38)))
])

class conf(dict):
    @classmethod
    def load(cls, filename):
        return yaml.load(open(filename), Loader=yaml.Loader)
    def __str__(self):
        return yaml.dump(self, default_flow_style=False, Dumper=yaml.SafeDumper)
    def __getattr__(self, key):
        return self[key]
    def __getitem__(self, key):
        assert key in self, f'"{key}" not found'
        return super().__getitem__(key)
yaml.Loader.add_constructor('tag:yaml.org,2002:map', lambda l, n: conf(l.construct_mapping(n)))
yaml.SafeDumper.add_representer(conf, lambda d, x: d.represent_dict(x))
CONF = {**CONF_DEFAULTS, **conf.load(CONF_PATH)}

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG if DEBUG else logging.INFO)
log.addHandler(logging.FileHandler(LOG_PATH))

from supr.state import localstate
state = localstate(DB_PATH, CONF['IDLE_TIMEOUT'])

# TODO: backend factory
if 'aws' in CONF:
    from supr.backend import aws
    backend = aws.backend()
    instance = aws.instance
else:
    assert False, "no backend configured"

