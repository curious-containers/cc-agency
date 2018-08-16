FROM docker.io/mongo:4.0

RUN apt-get update \
&& apt-get install -y python3-pip python3-venv libssl-dev \
&& pip3 install --no-input --user --upgrade poetry

ADD pyproject.toml /opt/cc-agency/pyproject.toml
ADD pyproject.lock /opt/cc-agency/pyproject.lock

ENV PATH ${PATH}:/root/.local/bin

WORKDIR /opt/cc-agency

RUN poetry install
