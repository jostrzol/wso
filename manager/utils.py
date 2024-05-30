IMGS_PATH = "/home/kuba/Studia/mgr/wso/wso/imgs"

def generate_timesrv_xml(name: str) -> str:
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
          <source file='{IMGS_PATH}/{name}.qcow2'/>
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