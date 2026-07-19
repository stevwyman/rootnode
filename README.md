# RootNode

![Python](https://img.shields.io/badge/Python-3.13-green.svg)
![Django](https://img.shields.io/badge/Django-5.1.7-green.svg)
[![Bootstrap](https://img.shields.io/badge/Bootstrap-7952B3?logo=bootstrap&logoColor=fff)]
[![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=fff)]

A simple gedcom based management for family trees based on Django framework and bootstrap. Ready to run in a container.

Main features:

- gedcom file import (web and cli)
- modification of data
- media management
- two factor authorizations
- multi language support
- face detection support (would need [facenode](https://github.com/stevwyman/facenode))

The main driver of initiating this project has been to focus on security. This type of information can be very sensitive and therefore **privacy** needs attention. On the other hand you want to share as much as possible to either help others or to get others information, if they have same.

Therefore we have have images that can be marked as private, but trees can be marked as public. When marked as public visitors can only see information that complies with the following rules:

- do not show birth events within the prior 110 years
- do not show death events within the prior 80 years
- do not show marriage events within the prior 60 years
- do not show individual/families where one of the above rule is a active

The above values can be of course configured.

![individual view](docu/individual_view.png "individual view")

![family view](docu/family_view.png "family view")

![media view](docu/media_view.png "media view")

## usage

```sh
python manage.py import_gedcom pfad/zur/datei.ged --tree-name "Familie Müller"
```

### deployment

Right now the default configuration is based on a local db.sqlite3
You might want to provide an volume to store the database and the stored data,
such as photos or documents.

```yaml
services:
  app:
    image: localhost/rootnode:latest
    container_name: genview
    ports:
      - 8003:8003
    volumes:
      - data:/data/genview:z
    env_file:
      - .env
      
volumes:
  data:
    name: genview_data
```

You can also provide configuration data using an .env file. Currently the following
parameters are supported:

```env
SECRET_KEY=django-insecure-u&zzp&ve-be0i^2ie*y!=3y_k3j_zd9q&yn!)b@g3j1rzy3pa(
ALLOWED_HOSTS=127.0.0.1
DEBUG=True

DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD=admin
```

## ToDos

- [ ] use hardened images, i.e. by Red Hat
- 
