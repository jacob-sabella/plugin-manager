# The host side of the emulator tests: QEMU (TCG on x86_64), the tools QEngine's builders need, and a VNC client for taps and
# screenshots. Runs with the Docker socket mounted, so QEngine's own privileged builder containers start as siblings.
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      qemu-system-arm qemu-utils qemu-efi-aarch64 ipxe-qemu python3 python3-pip python3-pil tesseract-ocr e2fsprogs file docker-cli git ca-certificates \
      curl unzip zip xz-utils cpio kmod procps iproute2 netcat-openbsd socat openssh-client util-linux dosfstools exfatprogs mtools sudo \
    && pip3 install --break-system-packages --no-cache-dir vncdotool \
    && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp/home
RUN mkdir -p /tmp/home && chmod 1777 /tmp/home
