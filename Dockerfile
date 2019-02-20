FROM circleci/python:3.7-node

RUN sudo npm install vamp-cli-ee -global --production

COPY . /usr/local/vamp-kmt/
RUN cd /usr/local/vamp-kmt && sudo npm install -global --production
