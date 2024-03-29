import sys
import traceback
from supr.backend import F_STATE, F_ACTIVE
from supr import CONF, DEBUG, instance, backend, state

def help():
    print("usage: supr [command] [args] | [instance] [command] [args]")
    print("commands:")
    for k, v in globals().items():
        if k.startswith('_'): continue
        if callable(v): print(f"  {k}")
def costs():
    for aws_id, runtime in state.get_runtime():
        i = backend.get_instance_by_id(aws_id)
        hours = runtime / 60 / 60
        print("{runtime:0.2f}h {name} ${cost:0.2f}".format(
            runtime=hours, 
            name=i.name, 
            cost=hours * i.conf['hour_cost']))

def main():
    def i(n, f=F_ACTIVE):
        x = backend.get_instance(n, f)
        print(x); return x if x else None
    def p(*f):
        [print(x) for x in backend.get_instances(*f)]
    def create(n, do_init=True):
        if i(n): print(f"instance {n} already exists"); return
        instance.new(n)
        i.init(); i(n) if do_init else None
    match sys.argv[1:]:
        case ['list']: p(F_ACTIVE)
        case ['list', 'all']: p(F_STATE['*'])
        case ['list', state]: p(F_STATE[state])
        case ['costs']: costs()
        case [name, 'conftest']: print(CONF[name])
        case [name, 'create']: create(name)
        case [name, 'create', '--no_init']: create(name, False)
        case [name, 'start']: i(name).start(); i(name)
        case [name, 'stop']: i(name).stop(); i(name)
        case [name, 'snapshot']: i(name).snapshot(); i(name)
        case [name, 'terminate']: input('terminate? (^c to cancel)'); i(name).terminate(); i(name, F_STATE['*'])
        case [name, 'ssh']: i(name).ssh()
        case [name, 'run', cmd, *args]: i(name).run(cmd, *args)
        case [name, 'install', src, pkg]: i(name).install(src, pkg)
        case [name, 'install_essential']: i(name).install_essential()
        case [name, 'install_base']: i(name).install_base()
        case [name, 'install_crontab']: i(name).install_crontab()
        case [name, 'attach_volumes']: i(name).attach_volumes()
        case [name, 'deploy']: i(name).deploy()
        case [name, cmd]: i(name).hook(cmd)
        case [name, cmd, *args]: i(name).hook(cmd, *args)
        case _: print("invalid command"); p(F_ACTIVE)

if __name__ == '__main__':
    try: main()
    except AssertionError as e:
        print(traceback.format_exc()) if DEBUG else print(f"invalid config: {e}")
    except Exception as e: raise e