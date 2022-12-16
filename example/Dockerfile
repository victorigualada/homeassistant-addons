ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /opt

# Install requirements for add-on
ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache build-base python3 python3-dev libffi-dev bluez

# RUN ln -sf python3 /usr/bin/python3
RUN python3 -m ensurepip
RUN pip3 install --no-cache --upgrade pip setuptools wheel

COPY requirements.txt .

RUN pip3 install -r requirements.txt
RUN pip3 uninstall pycrypto
RUN pip3 install pycryptodome

COPY bridge/__main__.py .
COPY bridge/nuki.py .
COPY run.sh /

# Generate nuki.yaml config file
RUN python3 . --generate-config > nuki.yaml

# Copy data for add-on
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
