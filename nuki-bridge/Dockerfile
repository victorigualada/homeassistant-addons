ARG BUILD_FROM=ghcr.io/hassio-addons/base-python/amd64:8.1.1
FROM $BUILD_FROM

WORKDIR /opt/nuki-bridge

ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache build-base python3 python3-dev libffi-dev bluez libc-dev

RUN python3 -m ensurepip
RUN pip3 install --no-cache --upgrade pip setuptools

COPY bridge/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bridge/__main__.py .
COPY bridge/nuki.py .
COPY run.sh /

RUN python3 . --generate-config > nuki.yaml

COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh", "/opt/nuki-bridge" ]
