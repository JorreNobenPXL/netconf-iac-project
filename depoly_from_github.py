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
# GITHUB FUNCTIONS
# -------------------------
def github_raw_url(device_name, filename):
    return f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/configs/{device_name}/{filename}"

def download_xml(device_name, filename):
    url = github_raw_url(device_name, filename)
    r = requests.get(url)
    if r.status_code != 200:
        raise Exception(f"Failed to download {filename} for {device_name} (HTTP {r.status_code})")
    return r.text

def list_device_configs(device_name):
    api_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/configs/{device_name}?ref={GITHUB_BRANCH}"
    r = requests.get(api_url)
    if r.status_code != 200:
        raise Exception(f"Cannot list configs for {device_name} (HTTP {r.status_code})")
    return sorted([item["name"] for item in r.json() if item["name"].endswith(".xml")])

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
    return node.text if node is not None else None

def apply_config(m, xml_config):
    try:
        print("      [+] Lock running")
        m.lock("running")
        print("      [+] Sending edit-config")
        m.edit_config(target="running", config=xml_config)
        print("      [+] edit-config OK")
    except RPCError as e:
        print("      [!] RPC Error!")
        print(e)
    finally:
        print("      [+] Unlock running")
        m.unlock("running")

# -------------------------
# CHECK FUNCTIONS
# -------------------------

# 01 Hostname
def check_hostname(m, desired_xml):
    desired = ET.fromstring(desired_xml).find(".//xe:hostname", NS).text
    running = extract_text(netconf_get_config(m, """
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native"><hostname/></native>
    """), ".//xe:hostname")
    print(f"      Current hostname: {running}")
    print(f"      Desired hostname: {desired}")
    return running != desired

# 02 Domain name
def check_domain_name(m, desired_xml):
    desired = ET.fromstring(desired_xml).find(".//xe:domain/xe:name", NS).text
    running = extract_text(netconf_get_config(m, """
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native"><ip><domain><name/></domain></ip></native>
    """), ".//xe:domain/xe:name")
    print(f"      Current domain: {running}")
    print(f"      Desired domain: {desired}")
    return running != desired

# 03 Domain lookup
def check_domain_lookup(m, desired_xml):
    desired = ET.fromstring(desired_xml).find(".//xe:lookup-conf/xe:lookup", NS).text
    running = extract_text(netconf_get_config(m, """
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native"><ip><domain><lookup-conf><lookup/></lookup-conf></domain></ip></native>
    """), ".//xe:lookup-conf/xe:lookup")
    print(f"      Current lookup: {running}")
    print(f"      Desired lookup: {desired}")
    return running != desired

# 04 SSH version
def check_ip_ssh_version(m, desired_xml):
    desired = ET.fromstring(desired_xml).find(".//xe:ssh/xe:version", NS).text
    running = extract_text(netconf_get_config(m, """
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native"><ip><ssh><version/></ssh></ip></native>
    """), ".//xe:ssh/xe:version")
    print(f"      Current SSH version: {running}")
    print(f"      Desired SSH version: {desired}")
    return running != desired

# 05 Physical interface no-shut
def check_interface_noshut(m, desired_xml):
    root = ET.fromstring(desired_xml)
    iface = root.find(".//xe:GigabitEthernet/xe:name", NS).text
    desired = root.find(".//xe:GigabitEthernet/xe:shutdown", NS).text
    running = extract_text(netconf_get_config(m, f"""
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
      <interface><GigabitEthernet><name>{iface}</name><shutdown/></GigabitEthernet></interface>
    </native>
    """), f".//xe:GigabitEthernet[xe:name='{iface}']/xe:shutdown")
    print(f"      Interface Gi{iface} shutdown: {running}")
    print(f"      Desired shutdown: {desired}")
    return running != desired

# 06 Subinterface IP
def check_subinterface_ip(m, desired_xml):
    root = ET.fromstring(desired_xml)
    iface = root.find(".//xe:GigabitEthernet/xe:name", NS).text
    desired_ip = root.find(".//xe:primary/xe:address", NS).text
    desired_mask = root.find(".//xe:primary/xe:mask", NS).text

    running_xml = netconf_get_config(m, f"""
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
      <interface><GigabitEthernet><name>{iface}</name>
        <ip><address><primary><address/><mask/></primary></address></ip>
      </GigabitEthernet></interface>
    </native>
    """)

    running_ip = extract_text(running_xml, f".//xe:GigabitEthernet[xe:name='{iface}']/xe:ip/xe:address/xe:primary/xe:address")
    running_mask = extract_text(running_xml, f".//xe:GigabitEthernet[xe:name='{iface}']/xe:ip/xe:address/xe:primary/xe:mask")

    print(f"      Subinterface Gi{iface} current: {running_ip} {running_mask}")
    print(f"      Desired: {desired_ip} {desired_mask}")

    return running_ip != desired_ip or running_mask != desired_mask

# 07 Helper-address
def check_subinterface_helper(m, desired_xml):
    root = ET.fromstring(desired_xml)
    iface = root.find(".//xe:GigabitEthernet/xe:name", NS).text
    desired = root.find(".//xe:helper-address/xe:helper-list/xe:helper-address", NS).text

    running = extract_text(netconf_get_config(m, f"""
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
      <interface><GigabitEthernet><name>{iface}</name>
        <ip><helper-address><helper-list><helper-address/></helper-list></helper-address></ip>
      </GigabitEthernet></interface>
    </native>
    """), f".//xe:GigabitEthernet[xe:name='{iface}']/xe:ip/xe:helper-address/xe:helper-list/xe:helper-address")

    print(f"      Helper Gi{iface}: {running}")
    print(f"      Desired helper: {desired}")

    return running != desired

# 08 OSPF router-id
def check_ospf_routerid(m, desired_xml):
    desired = ET.fromstring(desired_xml).find(".//ospf:router-id", NS).text
    running = extract_text(netconf_get_config(m, """
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
      <router><ospf xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-ospf"><router-id/></ospf></router>
    </native>
    """), ".//ospf:router-id")
    print(f"      Current router-id: {running}")
    print(f"      Desired router-id: {desired}")
    return running != desired

# -------------------------
# MAP XML FILES TO FUNCTIONS
# -------------------------
CHECK_FUNCTIONS = {
    "01_hostname.xml": check_hostname,
    "02_domain.xml": check_domain_name,
    "03_domain_lookup.xml": check_domain_lookup,
    "04_ip_ssh.xml": check_ip_ssh_version,
    "05_gi0_0_0_noshut.xml": check_interface_noshut,
    "06_gi0_0_0_10.xml": check_subinterface_ip,
    "07_gi0_0_0_20.xml": check_subinterface_ip,
    "08_gi0_0_0_30.xml": check_subinterface_ip,
    "09_gi0_0_0_40_ip.xml": check_subinterface_ip,
    "10_gi0_0_0_40_helper.xml": check_subinterface_helper,
    "11_gi0_0_1.xml": check_subinterface_ip,
    "12_ospf.xml": check_ospf_routerid,
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

    try:
        config_files = list_device_configs(name)
    except Exception as e:
        print(f"[!] Cannot load config list for {name}: {e}")
        return

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

                if filename in CHECK_FUNCTIONS:
                    needs_change = CHECK_FUNCTIONS[filename](m, desired_xml)

                    if not needs_change:
                        print(f"      [=] SKIP {filename} (already correct)")
                        continue

                    print(f"      [!] APPLY {filename}")
                    apply_config(m, desired_xml)

                else:
                    print(f"      [?] No check function for {filename}, applying anyway...")
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
