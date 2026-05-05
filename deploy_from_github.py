import json
import requests
import xml.etree.ElementTree as ET
from ncclient import manager
from ncclient.operations import RPCError

# -------------------------
# GITHUB SETTINGS
# -------------------------
GITHUB_USER = "JorreNobenPXL"
GITHUB_REPO = "netconf-iac-project"
GITHUB_BRANCH = "main"

# -------------------------
# NAMESPACES
# -------------------------
NS = {
    "nc": "urn:ietf:params:xml:ns:netconf:base:1.0",
    "xe": "http://cisco.com/ns/yang/Cisco-IOS-XE-native",
    "ospf": "http://cisco.com/ns/yang/Cisco-IOS-XE-ospf"
}

# -------------------------
# GITHUB DOWNLOAD
# -------------------------
def github_raw_url(device_name, filename):
    return f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/configs/{device_name}/{filename}"

def download_xml(device_name, filename):
    url = github_raw_url(device_name, filename)
    r = requests.get(url)

    if r.status_code != 200:
        raise Exception(f"Failed to download {filename} for {device_name} (HTTP {r.status_code})")

    return r.text

# -------------------------
# NETCONF HELPERS
# -------------------------
def netconf_get_config(m, filter_body):
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

def apply_config(m, xml_config):
    try:
        m.lock("running")
        m.edit_config(target="running", config=xml_config)
        print("      [+] edit-config OK")
    except RPCError as e:
        print("      [!] RPC Error!")
        print(e)
    finally:
        m.unlock("running")

# -------------------------
# CHECK FUNCTIONS
# -------------------------
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

    print(f"      Current hostname: {current_hostname}")
    print(f"      Desired hostname: {desired_hostname}")

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

    print(f"      Interface: GigabitEthernet{iface_name}")
    print(f"      Current desc: {current_desc}")
    print(f"      Desired desc: {desired_desc}")

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

    print(f"      Interface: GigabitEthernet{iface_name}")
    print(f"      Current IP: {current_ip} {current_mask}")
    print(f"      Desired IP: {desired_ip} {desired_mask}")

    return current_ip != desired_ip or current_mask != desired_mask


def check_ospf(m, desired_xml):
    desired_root = ET.fromstring(desired_xml)

    desired_process = desired_root.find(".//ospf:id", NS).text
    desired_routerid = desired_root.find(".//ospf:router-id", NS).text

    filter_body = f"""
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
      <router>
        <ospf xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-ospf">
          <id>{desired_process}</id>
          <router-id/>
        </ospf>
      </router>
    </native>
    """

    running_xml = netconf_get_config(m, filter_body)
    current_routerid = extract_text(running_xml, ".//ospf:router-id")

    print(f"      OSPF process: {desired_process}")
    print(f"      Current router-id: {current_routerid}")
    print(f"      Desired router-id: {desired_routerid}")

    return current_routerid != desired_routerid


CHECK_FUNCTIONS = {
    "01_hostname.xml": check_hostname,
    "02_int_desc.xml": check_interface_desc,
    "03_int_ip.xml": check_interface_ip,
    "04_ospf.xml": check_ospf
}

# -------------------------
# DEVICE DEPLOY
# -------------------------
def deploy_device(device):
    name = device["name"]
    ip = device["ip"]
    port = device.get("port", 830)
    username = device["username"]
    password = device["password"]

    print(f"\n==============================")
    print(f"[+] Deploying to {name} ({ip})")
    print(f"==============================")

    config_files = list(CHECK_FUNCTIONS.keys())

    try:
        with manager.connect(
            host=ip,
            port=port,
            username=username,
            password=password,
            hostkey_verify=False,
            allow_agent=False,
            look_for_keys=False
        ) as m:

            print("[+] Connected")

            for filename in config_files:
                print(f"\n   -> Processing {filename}")

                try:
                    desired_xml = download_xml(name, filename)
                except Exception as e:
                    print(f"      [!] Skipping {filename}: {e}")
                    continue

                needs_change = CHECK_FUNCTIONS[filename](m, desired_xml)

                if not needs_change:
                    print(f"      [=] SKIP {filename} (already correct)")
                    continue

                print(f"      [!] APPLY {filename}")
                apply_config(m, desired_xml)

            print(f"\n[+] Finished device {name}")

    except Exception as e:
        print(f"[!] Failed connecting to {name}: {e}")

# -------------------------
# MAIN
# -------------------------
def main():
    with open("devices.json", "r") as f:
        devices = json.load(f)

    for device in devices:
        deploy_device(device)

    print("\n[+] All devices deployment finished")

if __name__ == "__main__":
    main()