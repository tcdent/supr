from supr import log, state, backend
from supr.backend import F_ID, F_STATE

if __name__ == '__main__':
    log.debug('[supr.cron] - checking for idle instances')
    for _id in state.get_idle():
        for i in backend.get_instances(F_ID[_id], F_STATE['running']):
            if i.is_super: continue
            if 'auto_stop' in i.conf and not i.conf['auto_stop']: continue
            log.info(repr(i))
            log.info("instance has been idle > IDLE_TIMEOUT")
            i.stop()
            log.info(repr(i))

