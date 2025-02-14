#!/bin/bash
#
# provision vmss virtual machine
#

set -ex

sudo sed -i 's/1/0/' /etc/apt/apt.conf.d/20auto-upgrades || true
source /etc/os-release

function build_and_install_team()
{
    TEAM_DIR=$(echo /lib/modules/$(uname -r)/kernel/net/team)
    if sudo modprobe team 2>/dev/null || [ -e "$TEAM_DIR/team.ko" ]; then
        echo "The module team or $TEAM_DIR/team.ko exists."
        return
    fi

    [ -z "$WORKDIR" ] && WORKDIR=$(mktemp -d)
    cd $WORKDIR

    KERNEL_RELEASE=$(uname -r)
    KERNEL_MAINVERSION=$(echo $KERNEL_RELEASE | cut -d- -f1)
    EXTRAVERSION=$(echo $KERNEL_RELEASE | cut -d- -f2)
    LOCALVERSION=$(echo $KERNEL_RELEASE | cut -d- -f3)
    VERSION=$(echo $KERNEL_MAINVERSION | cut -d. -f1)
    PATCHLEVEL=$(echo $KERNEL_MAINVERSION | cut -d. -f2)
    SUBLEVEL=$(echo $KERNEL_MAINVERSION | cut -d. -f3)

    # Install the required debian packages to build the kernel modules
    apt-get -o DPkg::Lock::Timeout=600 install -y build-essential linux-headers-${KERNEL_RELEASE} autoconf pkg-config fakeroot
    apt-get -o DPkg::Lock::Timeout=600 install -y flex bison libssl-dev libelf-dev
    apt-get -o DPkg::Lock::Timeout=600 install -y libnl-route-3-200 libnl-route-3-dev libnl-cli-3-200 libnl-cli-3-dev libnl-3-dev

    # Add the apt source mirrors and download the linux image source code
    cp /etc/apt/sources.list /etc/apt/sources.list.bk
    sed -i "s/^# deb-src/deb-src/g" /etc/apt/sources.list
    apt-get -o DPkg::Lock::Timeout=600 update
    apt-get -o DPkg::Lock::Timeout=600 source linux-image-unsigned-$(uname -r) > source.log

    # Recover the original apt sources list
    cp /etc/apt/sources.list.bk /etc/apt/sources.list
    apt-get -o DPkg::Lock::Timeout=600 update

    # Build the Linux kernel module drivers/net/team
    cd $(find . -maxdepth 1 -type d | grep -v "^.$")
    make  allmodconfig
    mv .config .config.bk
    cp /boot/config-$(uname -r) .config
    grep NET_TEAM .config.bk >> .config
    echo CONFIG_NET_VENDOR_MICROSOFT=y >> .config
    echo CONFIG_MICROSOFT_MANA=m >> .config
    echo CONFIG_SYSTEM_REVOCATION_LIST=n >> .config
    make VERSION=$VERSION PATCHLEVEL=$PATCHLEVEL SUBLEVEL=$SUBLEVEL EXTRAVERSION=-${EXTRAVERSION} LOCALVERSION=-${LOCALVERSION} modules_prepare
    make M=drivers/net/team

    # Install the module
    mkdir -p $TEAM_DIR
    cp drivers/net/team/*.ko $TEAM_DIR/
    modinfo $TEAM_DIR/team.ko
    depmod
    modprobe team
    cd /tmp
    rm -rf $WORKDIR
}

function install_packages(){
    # install docker
    apt-get -o DPkg::Lock::Timeout=600 update
    apt-get -o DPkg::Lock::Timeout=600 install -y \
        apt-transport-https \
        ca-certificates \
        curl \
        gnupg-agent \
        software-properties-common || return

    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | apt-key add -

    add-apt-repository \
       "deb [arch=amd64] https://download.docker.com/linux/ubuntu \
       $(lsb_release -cs) \
       stable"

    apt-get -o DPkg::Lock::Timeout=600 update
    apt-get -o DPkg::Lock::Timeout=600 install -y docker-ce docker-ce-cli containerd.io || return

    # install qemu for multi-arch docker
    #apt-get -o DPkg::Lock::Timeout=600 install -y qemu binfmt-support qemu-user-static

    # install utilities for image build
    apt-get -o DPkg::Lock::Timeout=600 install -y make || return
    apt-get -o DPkg::Lock::Timeout=600 install -y python3-pip || return
    pip3 install --force-reinstall --upgrade jinja2==2.10 || return
    pip3 install j2cli==0.3.10 markupsafe==2.0.1 || return
    # for team services agent
    apt-get -o DPkg::Lock::Timeout=600 install -y python-is-python2
    # install python2 libvirt 5.10.0
    apt-get -o DPkg::Lock::Timeout=600 install -y python2-dev python-pkg-resources libvirt-dev pkg-config || return
    curl https://bootstrap.pypa.io/pip/2.7/get-pip.py | python2
    pip2 install libvirt-python==5.10.0
    pip2 install docker==4.4.1

    # install packages for vs test
    pip3 install pytest==4.6.2 attrs==19.1.0 exabgp==4.0.10 distro==1.5.0 docker==4.4.1 redis==3.3.4
    apt-get -o DPkg::Lock::Timeout=600 install -y libhiredis0.14 || return

    # install packages for kvm test
    apt-get -o DPkg::Lock::Timeout=600 install -y libvirt-clients \
        qemu \
        openvswitch-switch \
        net-tools \
        bridge-utils \
        util-linux \
        iproute2 \
        vlan \
        apt-transport-https \
        ca-certificates \
        curl \
        software-properties-common \
        python3-libvirt \
        libzmq3-dev \
        libzmq5 \
        libboost-serialization1.71.0 \
        uuid-dev || return
}

for ((i=0;i<10;i++));do
    install_packages && break
    sleep 30
done

# install br_netfilter kernel module
modprobe br_netfilter

# build install team kernel module
build_and_install_team

# set sysctl bridge parameters for testbed
sysctl -w net.bridge.bridge-nf-call-arptables=0
sysctl -w net.bridge.bridge-nf-call-ip6tables=0
sysctl -w net.bridge.bridge-nf-call-iptables=0

# set sysctl RCVBUF default parameter for testbed
sysctl -w net.core.rmem_default=509430500

# enable traffic forward
/usr/sbin/iptables -A FORWARD -j ACCEPT

# enable nat
iptables -t nat -A POSTROUTING -s 10.250.0.0/24 -o eth0 -j MASQUERADE

# echo add tmp user so that AzDevOps user id will be 1002.
# this is needed as sonic-mgmt container has an user id 1001 already
useradd -M sonictmp

# echo creating tmp AzDevOps account
tmpuser=AzDevOps
useradd -m $tmpuser
usermod -a -G docker $tmpuser
usermod -a -G adm $tmpuser
usermod -a -G sudo $tmpuser
echo "$tmpuser ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/100-$tmpuser
chmod 440 /etc/sudoers.d/100-$tmpuser

sudo pip3 install docker==6.1.0 requests==2.31.0
sudo apt-get -o DPkg::Lock::Timeout=600 install libyang0.16 libboost1.71-dev libboost-dev

# create two partition on the 1T data disk
# first partition for azure pipeline agent
# second partition for data
# find data disk, assume it is 1T
datadisk=$(lsblk -d  | grep -E '[[:space:]]1T[[:space:]]' | awk '{print $1}')
sgdisk -n 0:0:500G -t 0:8300 -c 0:agent /dev/$datadisk
sgdisk -n 0:0:0 -t 0:8300 -c 0:data /dev/$datadisk
mkfs.ext4 /dev/${datadisk}1
mkfs.ext4 /dev/${datadisk}2

mkdir /agent
mount /dev/${datadisk}1 /agent
mkdir /data
mount /dev/${datadisk}2 /data

# clone sonic-mgmt repo
pushd /data
git clone https://github.com/Azure/sonic-mgmt
chown -R $tmpuser.$tmpuser /data/sonic-mgmt
popd
