from pathlib import Path


def vm_conf(imgs_path: Path, name: str) -> str:
    return f"""
    <domain type='kvm'>
      <name>{name}</name>
      <memory unit='MiB'>512</memory>
      <vcpu>1</vcpu>
      <os>
        <type arch='x86_64' machine='pc-i440fx-2.9'>hvm</type>
        <boot dev='hd'/>
      </os>
      <devices>
        <disk type='file' device='disk'>
          <driver name='qemu' type='qcow2'/>
          <source file='{imgs_path}/{name}.qcow2'/>
          <target dev='vda' bus='virtio'/>
          <address type='pci' domain='0x0000' bus='0x00' slot='0x04' function='0x0'/>
        </disk>
        <interface type='bridge'>
          <source bridge='virbr0'/>
          <model type='virtio'/>
          <address type='pci' domain='0x0000' bus='0x00' slot='0x03' function='0x0'/>
        </interface>
        <graphics type='vnc' port='-1' listen='0.0.0.0'/>
         <channel type='unix'>
          <target type='virtio' name='org.qemu.guest_agent.0'/>
        </channel>
      </devices>
    </domain>
    """


def generate_nginx_conf(server_ips, imgs_path):
    server_block = "\n".join([f"    server {ip};" for ip in server_ips])

    nginx_conf = f"""
    user nginx;

    worker_processes auto;

    pcre_jit on;

    error_log /var/log/nginx/error.log warn;

    include /etc/nginx/modules/*.conf;

    include /etc/nginx/conf.d/*.conf;

    events {{
      worker_connections 1024;
    }}

    http {{
        upstream backend {{
      {server_block}
        }}

        server {{
            listen 80;

            location / {{
                proxy_pass http://backend;
            }}
        }}
    }}
    """

    with open(f"{imgs_path}/../ansible/setup_nginx/nginx.conf", "w") as f:
        f.write(nginx_conf)


if __name__ == "__main__":
    generate_nginx_conf(["192.168.122.91"], "/home/kuba/Studia/mgr/proj/wso/imgs")
