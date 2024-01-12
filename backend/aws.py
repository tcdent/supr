import boto3
from supr import DEBUG, CONF
from supr.backend import baseinstance, basebackend


class instance(baseinstance):
    id = property(lambda self: self.meta.id)
    name = property(lambda self: next((t['Value'] for t in self.meta.tags or [] if t['Key'] == 'Name'), 'none'))
    state = property(lambda self: self.meta.state['Name'])
    tags = property(lambda self: [t['Value'] for t in self.meta.tags or [] if t['Key'] != 'Name'])
    public_ip_address = property(lambda self: self.meta.public_ip_address)
    private_ip_address = property(lambda self: self.meta.private_ip_address)
    instance_type = property(lambda self: self.meta.instance_type)
    aws_key = property(lambda self: self.conf['aws']['key'])
    aws_secret = property(lambda self: self.conf['aws']['secret'])
    def _start(self): return self.meta.start()
    def _stop(self): return self.meta.stop()
    def _terminate(self): return self.meta.terminate()
    def wait_until_running(self): self.meta.wait_until_running()
    def wait_until_stopped(self): self.meta.wait_until_stopped()
    def wait_until_terminated(self): self.meta.wait_until_terminated()
    def __init__(self, meta):
        self.meta = meta
        self._storage_handlers.append(('aws:s3', lambda self, name: self._attach_bucket(name)))
        self._storage_handlers.append(('aws:ebs',lambda self, name: self._attach_storage(name)))       
    def _attach_bucket(self, name):
        assert name in self.conf.get('volumes', ())
        conf, passwd = self.conf['volumes'][name], self.home/'.aws/passwd'
        assert conf.get('provider') == 'aws:s3'
        assert {'id', 'mount'}.issubset(conf)
        self.cmd(f"mkdir -p {self.home/'.aws'}")
        self.cmd(f"echo '{self.aws_key}:{self.aws_secret}' > {passwd}", hide=True)
        self.cmd(f"chmod 600 {passwd}")
        self.cmd(f"sudo mkdir -p {conf['mount']}")
        self.cmd(f"sudo umount {conf['mount']}", warn=True)
        self.cmd(f"sudo s3fs {conf['id']} {conf['mount']} -o allow_other,ensure_diskfree=1024,umask=0000,passwd_file={passwd}")
        # TODO options from config file
    def _attach_storage(self, name):
        conf = self.conf['volumes'][name]
        assert conf.get('provider') == 'aws:ebs'
        if not 'id' in conf: return
        if conf['id'] in [v.volume_id for v in self.meta.volumes.all()]: return # already attached
        # TODO get next available dev name; this doesn't even show up in the instance this way
        self.meta.attach_volume(
            Device="/dev/sdg", #conf_v['dev'], 
            VolumeId=conf['id'])
        self.meta.wait_until_running()
        self.mount(name)
    def _snapshot(self, name) -> str:
        self.wait_until_running()
        ami = self.meta.create_image(Name=name)
        self.client.get_waiter('image_available').wait(ImageIds=[ami.id])
        return ami.id

class backend(basebackend):
    @property
    def client(self):
        if not hasattr(self, '_client'):
            c = CONF['base']['aws']
            assert {'key', 'secret', 'region'}.issubset(c)
            self._session = boto3.Session(c['key'], c['secret'], region_name=c['region'])
            self._client = self._session.resource('ec2')
        return self._client
    def get_instance_by_id(self, _id) -> instance:
        from supr import instance
        return instance(self.client.Instance(_id))
    def get_instances(self, *filters) -> list[instance]:
        from supr import instance
        return [instance(r) for r in self.client.instances.filter(Filters=filters)]
    def create_instance(self, name) -> instance:
        from supr import instance
        conf = instance.get_conf(name)
        assert 'aws:type' in conf
        i = dict(
            InstanceType=conf['aws:type'],
            MaxCount=1, MinCount=1)
        if 'aws:ami' in conf:
            assert {'key_pair', 'aws:security_groups', 'aws:subnet'}.issubset(conf)
            assert 'aws:name' in conf['key_pair']
            i.update(dict(
                ImageId=conf['aws:ami'], 
                KeyName=conf['key_pair']['aws:name'], 
                SecurityGroupIds=conf['aws:security_groups'], 
                SubnetId=conf['aws:subnet']))
        elif 'aws:template' in conf:
            assert {'id', 'version'}.issubset(conf['aws:template'])
            i['LaunchTemplate'] = dict(
                LaunchTemplateId=conf['aws:template']['id'],
                Version=str(conf['aws:template']['version']))
        else: assert False, "either aws:ami or aws:template is required"
        for v_name, v_conf in conf.get('volumes', {}).items():
            # TODO these rules are pretty opaque
            if 'aws:template' in conf and not 'id' in v_conf: continue
            if v_conf['provider'] != 'aws:ebs' or 'id' in v_conf: continue
            b = dict(
                DeviceName=v_conf.get('dev', "/dev/xvda"),
                Ebs=dict(
                    VolumeSize=v_conf['size'], 
                    DeleteOnTermination=not v_conf.get('persist', False), 
                    VolumeType=v_conf.get('type', 'gp3')))
            i['BlockDeviceMappings'] = i.get('BlockDeviceMappings', [])
            i['BlockDeviceMappings'].append(b)
        if DEBUG: print(i); input('create?')
        r = self.client.create_instances(**i)[0]
        r.create_tags(Tags=[dict(Key='Name', Value=name)])
        return instance(r)

