ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /opt

# Install requirements for add-on
ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache build-base python3.9 libffi-dev bluez

RUN ln -sf python3.9 /usr/bin/python
RUN python3 -m ensurepip
RUN pip3 install --no-cache --upgrade pip setuptools

COPY requirements.txt .

RUN pip install -r requirements.txt
RUN pip uninstall pycrypto
RUN pip install pycryptodome

COPY bridge/__main__.py .
COPY bridge/nuki.py .
COPY run.sh /

# Generate nuki.yaml config file
RUN python . --generate-config > nuki.yaml

# Copy data for add-on
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
