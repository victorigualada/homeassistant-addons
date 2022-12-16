ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /opt

# Install requirements for add-on
ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache build-base python3 python3-dev libffi-dev bluez

# RUN ln -sf python3 /usr/bin/python3
RUN python3 -m ensurepip
RUN pip3 install --no-cache --upgrade pip setuptools wheel
RUN pip3 install --no-cache --upgrade aiohttp==3.8.1 bleak==0.14.2 crc16==0.1.1 PyYAML==6.0

# RUN pip3 uninstall pycrypto
# RUN pip3 install pycryptodome

COPY bridge/__main__.py .
COPY bridge/nuki.py .
COPY run.sh /

# Generate nuki.yaml config file
RUN python3 . --generate-config > nuki.yaml

# Copy data for add-on
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]

#ARG BUILD_FROM=ghcr.io/hassio-addons/base-python/amd64:8.1.1
## hadolint ignore=DL3006
#FROM ${BUILD_FROM}
#
## Copy requirements.txt
#COPY requirements.txt __main__.py nuki.py /opt/
#
## Set workdir
#WORKDIR /opt
#
## Set shell
#SHELL ["/bin/bash", "-o", "pipefail", "-c"]
#
## Install requirements for add-on
#RUN apk add --no-cache --virtual .build-dependencies \
#    libc-dev=0.7.2-r3 \
#    py3-pip=20.3.4-r1 \
#    python3-dev=3.9.7-r4 \
#
#RUN apk add --no-cache \
#    build-base \
#    python3=3.9.7-r4 \
#    libffi-dev=3.4.2-r1 \
#    bluez=5.64-r0 \
#    #bluez=5.50-1.2 \
#
#RUN pip install --no-cache-dir -r /opt/requirements.txt
#
## Copy data for add-on
#COPY run.sh /
#RUN chmod u+x /run.sh
#
#CMD [ "/run.sh" ]
