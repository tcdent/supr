import os, sys
from datetime import timedelta
import logging
import yaml

DEBUG = os.environ.get('DEBUG', False)
CONF_PATH = os.environ.get('SUPR_CONF', 'supr.yaml')
IDLE_TIMEOUT = timedelta(minutes=15)
LOCAL_EXCLUDE = ('.env', '.git', '.github', ) # rsync exlcude patterns

DIR = os.path.dirname(os.path.abspath(__file__))
ANSI = dict([
    (v, "\033[%sm" % i) for v, i in \
        list(zip(['RST', 'BLD', 'DIM', 'ITL', 'UDL', 'SBL', 'FBL', 'REV'], range(8))) + \
        list(zip(['BLK', 'RED', 'GRN', 'YEL', 'BLU', 'PNK', 'CYN', 'WHT'], range(30, 38)))
])

class conf(dict):
    @classmethod
    def load(cls, filename): return yaml.load(open(filename), Loader=yaml.Loader)
    def __str__(self): return yaml.dump(self, default_flow_style=False, Dumper=yaml.SafeDumper)
    def __getattr__(self, key): return self[key]
    def __getitem__(self, key):
        assert key in self, f'"{key}" not found'
        return super().__getitem__(key)
yaml.Loader.add_constructor('tag:yaml.org,2002:map', lambda l, n: conf(l.construct_mapping(n)))
yaml.SafeDumper.add_representer(conf, lambda d, x: d.represent_dict(d, x))
CONF = conf.load(CONF_PATH)

LOG_PATH = os.path.join(DIR, 'log')
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG if DEBUG else logging.INFO)
log.addHandler(logging.FileHandler(LOG_PATH))

from supr.state import localstate
state = localstate('.supr.db')

if 'aws' in CONF:
    from supr.backend import aws
    backend = aws.backend()
    instance = aws.instance
else:
    assert False, "no backend configured"

