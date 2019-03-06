FROM circleci/python:3.7-node

RUN sudo npm install vamp-cli-ee -global --production

COPY . /usr/local/vamp-kmt/
RUN cd /usr/local/vamp-kmt && sudo npm install -global --production

RUN base=https://github.com/magneticio/forklift/releases/download/0.1.0 && \
    curl -L $base/forklift-$(uname -s)-$(uname -m) > /tmp/forklift && \
    sudo mv /tmp/forklift /usr/local/bin/forklift && \
    sudo chmod +x /usr/local/bin/forklift
