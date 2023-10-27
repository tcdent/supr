supr
====
`supr` is an irresponsible implementation of a AWS EC2 instance manager. 
It is irresponsible because it does not care about your data. It is
irresponsible because it does not care about your feelings. It is
irresponsible because it does not care about your feelings about your
data. *--GitHub Copilot*

Comments are almost non-existant, but the code is short and fairly 
straigtforward. Some questionable tricks have been employed for
entertainment purposes. I don't expect anyone will ever use this 
in production. I had fun writing it. 

## Installation
`supr` is available on PyPI and can be installed with `pip`:
    ```pip install supr```

## Usage
`supr` is a command line tool. Commands are listed in `__main__.py`. 

Some expected ones are:

`supr list`
`supr list running`

`supr [instance] start`
`supr [instance] stop`

`supr [instance] ssh`
`supr [instance] cmd [command]`

`supr [instance] deploy`

## Motivations
This was conceived as a way to economically implement LLM's on AWS.
Some of the design decisions made reflect this and deviate from sane
defaults.

### Timeouts
`supr` uses timeouts to prevent instances from idling if forgotten. This
is not always desirable and can be disabled with `super:true` or `auto_stop:false`.

### Scaling Instances
`supr` will not start an instance if there is already an instance with
the same name. Support for multiple instances with the same config
should be added in the future along with the boilerplate necessary
to support a distributed LLM.

## Configuration
`supr` is configured with a YAML file `./supr.yaml`.

### Example
```yaml
aws: &aws
  aws:
    key: # AWS key
    secret: # AWS secret
    region: us-west-2
  key_pair:
    aws:name: # AWS key pair name
    file: # path to private key
  aws:security_groups:
  - sg-1234567890abcdef0
  aws:subnet: subnet-1234567890abcdef0

packages:
  compute-base: &packages_compute-base
  - apt:cmake
  - pip:numpy
  - pip:pyopencl
  compute-app: &packages_compute-app
  - local:deploy # Path to a local package

systems:
  debian-12: &systems_debian-12
    dist_release: bookworm
    aws:ami: ami-1234567890abcdef0
  debian-12-contrib: &systems_debian-12-contrib
    <<: *systems_debian-12
    apt_sources:
    - contrib
    - non-free
    - non-free-firmware

sizes:
  micro: &sizes_micro
    aws:type: t4g.micro
    hour_cost: 0.0084
  compute-medium: &sizes_compute-small
    aws:type: g5g.2xlarge
    hour_cost: 0.556

volumes: &volumes_shared
  files:
    provider: aws:s3
    id: files # AWS S3 bucket name
    mount: /mnt/files

base: &base
  <<: *aws
  user: admin
  env: /home/admin/.env
  vars: &base_vars
    TERM: xterm-256color
    DEBIAN_FRONTEND: noninteractive
  volumes:
    <<: *volumes_shared

supr:
  super: true # this is a supervisor instance
  <<: *base
  <<: *sizes_micro
  <<: *systems_debian-12
  env: /home/admin/.env
  volumes: 
    <<: *volumes_shared
    root:
      provider: aws:ebs
      dev: /dev/xvda
      size: 16
  crontab:
  - "*/5 * * * * /home/admin/.env/bin/python -m supr.cron"
  packages:
    base: 
    - apt:python-pip
    app:
    - pip:supr

www:
    auto_stop: false # run until manually stopped
    <<: *base
    <<: *sizes_micro
    <<: *systems_debian-12
    env: /home/admin/.env
    packages:
        base: 
        - apt:apache2
        app:
        - local:deploy
    entrypoint: bash deploy/entrypoint.sh

compute-base: &compute-base
  <<: *base
  <<: *systems_debian-12-contrib
  vars:
    <<: *base_vars
    CC: /usr/bin/gcc-11
    CXX: /usr/bin/g++-11
    CUDAHOSTCXX: /usr/bin/g++-11

compute-small:
  <<: *compute-base
  <<: *sizes_compute-small
  aws:ami: ami-05c3943f51e2398ca
  volumes:
    <<: *volumes_shared
  packages:
    base: *packages_compute-base
    app: *packages_compute-app
  entrypoint: bash deploy/entrypoint.sh
```


