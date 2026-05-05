import os
import xml.etree.ElementTree as ET
from ncclient import manager
from ncclient.operations import RPCError

ROUTER = "192.168.50.1"
USERNAME = "automation"
PASSWORD = "test"

CONFIG_DIR = "configs"

NS = {
    "nc": "urn:ietf:params:xml:ns:netconf:base:1.0",
    "xe": "http://cisco.com/ns/yang/Cisco-IOS-XE-native"
}

# ------------------------
# Helper functions
# ------------------------

def load_xml_file(path):
    with open(path, "r") as f:
        return f.read()

def netconf_get_config(m, filter_body):
    # filter_body should NOT include <filter> wrapper
    filter_xml = f"""
    <filter xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
        {filter_body}
    </filter>
    """
    return m.get_config(source="running", filter=filter_xml).data_xml

def extract_text(xml_data, xpath):
    root = ET.fromstring(xml_data)
    node = root.find(xpath, NS)
    if node is not None:
        return node.text
    return None


# ------------------------
# Check functions
# ------------------------

def check_hostname(m, desired_xml):
    desired_root = ET.fromstring(desired_xml)
    desired_hostname = desired_root.find(".//xe:hostname", NS).text

    filter_body = """
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
        <hostname/>
    </native>
    """

    running_xml = netconf_get_config(m, filter_body)
    current_hostname = extract_text(running_xml, ".//xe:hostname")

    print(f"   Current hostname: {current_hostname}")
    print(f"   Desired hostname: {desired_hostname}")

    return current_hostname != desired_hostname


def check_interface_desc(m, desired_xml):
    desired_root = ET.fromstring(desired_xml)

    iface_name = desired_root.find(".//xe:GigabitEthernet/xe:name", NS).text
    desired_desc = desired_root.find(".//xe:GigabitEthernet/xe:description", NS).text

    filter_body = f"""
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
        <interface>
            <GigabitEthernet>
                <name>{iface_name}</name>
                <description/>
            </GigabitEthernet>
        </interface>
    </native>
    """

    running_xml = netconf_get_config(m, filter_body)
    xpath = f".//xe:GigabitEthernet[xe:name='{iface_name}']/xe:description"
    current_desc = extract_text(running_xml, xpath)

    print(f"   Interface: GigabitEthernet{iface_name}")
    print(f"   Current desc: {current_desc}")
    print(f"   Desired desc: {desired_desc}")

    return current_desc != desired_desc


def check_interface_ip(m, desired_xml):
    desired_root = ET.fromstring(desired_xml)

    iface_name = desired_root.find(".//xe:GigabitEthernet/xe:name", NS).text
    desired_ip = desired_root.find(".//xe:primary/xe:address", NS).text
    desired_mask = desired_root.find(".//xe:primary/xe:mask", NS).text

    filter_body = f"""
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
        <interface>
            <GigabitEthernet>
                <name>{iface_name}</name>
                <ip>
                    <address>
                        <primary>
                            <address/>
                            <mask/>
                        </primary>
                    </address>
                </ip>
            </GigabitEthernet>
        </interface>
    </native>
    """

    running_xml = netconf_get_config(m, filter_body)

    xpath_ip = f".//xe:GigabitEthernet[xe:name='{iface_name}']/xe:ip/xe:address/xe:primary/xe:address"
    xpath_mask = f".//xe:GigabitEthernet[xe:name='{iface_name}']/xe:ip/xe:address/xe:primary/xe:mask"

    current_ip = extract_text(running_xml, xpath_ip)
    current_mask = extract_text(running_xml, xpath_mask)

    print(f"   Interface: GigabitEthernet{iface_name}")
    print(f"   Current IP: {current_ip} {current_mask}")
    print(f"   Desired IP: {desired_ip} {desired_mask}")

    if current_ip != desired_ip or current_mask != desired_mask:
        return True

    return False

def check_ospf(m, desired_xml):
    desired_root = ET.fromstring(desired_xml)

    desired_process = desired_root.find(".//xe:ospf/xe:id", NS)
    if desired_process is None:
        print("   [!] No OSPF process ID found in desired XML")
        return True

    desired_process = desired_process.text

    filter_body = f"""
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
      <router>
        <ospf xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-ospf">
          <id>{desired_process}</id>
        </ospf>
      </router>
    </native>
    """

    running_xml = netconf_get_config(m, filter_body)

    # Check if ospf process exists
    root = ET.fromstring(running_xml)

    # extra namespace for ospf module
    ns2 = NS.copy()
    ns2["ospf"] = "http://cisco.com/ns/yang/Cisco-IOS-XE-ospf"

    ospf_id = root.find(".//ospf:ospf/ospf:id", ns2)

    if ospf_id is None:
        print("   Current OSPF: not configured")
        print(f"   Desired OSPF: process {desired_process}")
        return True

    print(f"   Current OSPF process: {ospf_id.text}")
    print(f"   Desired OSPF process: {desired_process}")

    # simpel check: als process bestaat -> skip
    # (voor echte idempotency kan je router-id en network statements ook vergelijken)
    return ospf_id.text != desired_process


# ------------------------
# Deployment logic
# ------------------------

CHECK_FUNCTIONS = {
    "01_hostname.xml": check_hostname,
    "02_int_desc.xml": check_interface_desc,
    "03_int_ip.xml": check_interface_ip,
    "04_ospf.xml": check_ospf
}

def deploy_file(m, filename):
    file_path = os.path.join(CONFIG_DIR, filename)
    xml_config = load_xml_file(file_path)

    if filename not in CHECK_FUNCTIONS:
        print(f"[!] No check function for {filename}, applying blindly...")
        apply_config(m, xml_config)
        return

    print(f"[+] Checking {filename} ...")
    needs_change = CHECK_FUNCTIONS[filename](m, xml_config)

    if not needs_change:
        print(f"[=] SKIP {filename} (already correct)")
        return

    print(f"[!] APPLY {filename} (difference detected)")
    apply_config(m, xml_config)


def apply_config(m, xml_config):
    try:
        m.lock("running")
        m.edit_config(target="running", config=xml_config)
        print("   [+] edit-config OK")
    except RPCError as e:
        print("   [!] RPC Error!")
        print(e)
    finally:
        m.unlock("running")


def main():
    files = sorted([f for f in os.listdir(CONFIG_DIR) if f.endswith(".xml")])

    with manager.connect(
        host=ROUTER,
        port=830,
        username=USERNAME,
        password=PASSWORD,
        hostkey_verify=False,
        allow_agent=False,
        look_for_keys=False
    ) as m:

        print("[+] Connected to device")

        for f in files:
            deploy_file(m, f)

        print("[+] Deployment complete")

if __name__ == "__main__":
    main()