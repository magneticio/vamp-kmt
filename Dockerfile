FROM circleci/python:3.7

ENV FORKLIFT_VERSION 0.1.16
ENV KUBECTL_VERSION 1.14.2
ENV KUSTOMIZE_VERSION 2.0.3

# Install envsubst
RUN sudo apt-get update && \
    sudo apt-get -y install gettext-base && \
    sudo apt-get clean && \
    sudo rm -rf /var/lib/apt/lists/*

# Install forklift
RUN sudo curl -L https://github.com/magneticio/forklift/releases/download/v${FORKLIFT_VERSION}/forklift-linux-amd64  -o /usr/bin/forklift && \
    sudo chmod +x /usr/bin/forklift
    
# Install kubectl
RUN sudo curl -L https://storage.googleapis.com/kubernetes-release/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl -o /usr/bin/kubectl && \
    sudo chmod +x /usr/bin/kubectl

# Install kustomize
RUN sudo curl -L https://github.com/kubernetes-sigs/kustomize/releases/download/v${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_amd64  -o /usr/bin/kustomize && \
    sudo chmod +x /usr/bin/kustomize

# Install vamp-kmt  
COPY requirements.txt /tmp
RUN sudo pip install -r /tmp/requirements.txt

COPY vamp-kmt.py /tmp
RUN echo \#\!$(which python3) | cat - /tmp/vamp-kmt.py > /tmp/vamp-kmt && \
    sudo mv /tmp/vamp-kmt /usr/bin/vamp-kmt && \
    sudo chmod +x /usr/bin/vamp-kmt
