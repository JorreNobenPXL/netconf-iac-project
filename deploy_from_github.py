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

    data = r.json()

    xml_files = []
    for item in data:
        if item["name"].endswith(".xml"):
            xml_files.append(item["name"])

    xml_files.sort()
    return xml_files

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

    desired_networks = []
    for net in desired_root.findall(".//ospf:network", NS):
        ip = net.find("ospf:ip", NS).text
        wildcard = net.find("ospf:wildcard", NS).text
        area = net.find("ospf:area", NS).text
        desired_networks.append((ip, wildcard, area))

    filter_body = f"""
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
      <router>
        <ospf xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-ospf">
          <id>{desired_process}</id>
          <router-id/>
          <network/>
        </ospf>
      </router>
    </native>
    """

    running_xml = netconf_get_config(m, filter_body)

    current_routerid = extract_text(running_xml, ".//ospf:router-id")

    current_networks = []
    running_root = ET.fromstring(running_xml)

    for net in running_root.findall(".//ospf:network", NS):
        ip = net.find("ospf:ip", NS).text
        wildcard = net.find("ospf:wildcard", NS).text
        area = net.find("ospf:area", NS).text
        current_networks.append((ip, wildcard, area))

    desired_networks.sort()
    current_networks.sort()

    print(f"      OSPF process: {desired_process}")
    print(f"      Current router-id: {current_routerid}")
    print(f"      Desired router-id: {desired_routerid}")
    print(f"      Current networks: {current_networks}")
    print(f"      Desired networks: {desired_networks}")

    if current_routerid != desired_routerid:
        return True

    if current_networks != desired_networks:
        return True

    return False

def check_vlan(m, desired_xml):
    desired_root = ET.fromstring(desired_xml)

    vlan_id = desired_root.find(".//xe:vlan/xe:vlan-list/xe:id", NS).text
    vlan_name_node = desired_root.find(".//xe:vlan/xe:vlan-list/xe:name", NS)
    desired_name = vlan_name_node.text if vlan_name_node is not None else None

    filter_body = f"""
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
      <vlan>
        <vlan-list>
          <id>{vlan_id}</id>
          <name/>
        </vlan-list>
      </vlan>
    </native>
    """

    running_xml = netconf_get_config(m, filter_body)

    current_name = extract_text(running_xml, ".//xe:vlan-list/xe:name")

    print(f"      VLAN ID: {vlan_id}")
    print(f"      Current VLAN name: {current_name}")
    print(f"      Desired VLAN name: {desired_name}")

    if current_name is None:
        return True

    if desired_name is not None and current_name != desired_name:
        return True

    return False


def check_svi_vlan_ip(m, desired_xml):
    desired_root = ET.fromstring(desired_xml)

    vlan_id = desired_root.find(".//xe:Vlan/xe:name", NS).text
    desired_ip = desired_root.find(".//xe:primary/xe:address", NS).text
    desired_mask = desired_root.find(".//xe:primary/xe:mask", NS).text

    filter_body = f"""
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
      <interface>
        <Vlan>
          <name>{vlan_id}</name>
          <ip>
            <address>
              <primary>
                <address/>
                <mask/>
              </primary>
            </address>
          </ip>
        </Vlan>
      </interface>
    </native>
    """

    running_xml = netconf_get_config(m, filter_body)

    xpath_ip = f".//xe:Vlan[xe:name='{vlan_id}']/xe:ip/xe:address/xe:primary/xe:address"
    xpath_mask = f".//xe:Vlan[xe:name='{vlan_id}']/xe:ip/xe:address/xe:primary/xe:mask"

    current_ip = extract_text(running_xml, xpath_ip)
    current_mask = extract_text(running_xml, xpath_mask)

    print(f"      SVI VLAN: {vlan_id}")
    print(f"      Current IP: {current_ip} {current_mask}")
    print(f"      Desired IP: {desired_ip} {desired_mask}")

    return current_ip != desired_ip or current_mask != desired_mask


def check_default_gateway(m, desired_xml):
    desired_root = ET.fromstring(desired_xml)

    desired_gw = desired_root.find(".//xe:default-gateway", NS).text

    filter_body = """
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
      <ip>
        <default-gateway/>
      </ip>
    </native>
    """

    running_xml = netconf_get_config(m, filter_body)

    current_gw = extract_text(running_xml, ".//xe:default-gateway")

    print(f"      Current default gateway: {current_gw}")
    print(f"      Desired default gateway: {desired_gw}")

    return current_gw != desired_gw


def check_switchport_access_vlan(m, desired_xml):
    desired_root = ET.fromstring(desired_xml)

    ports = desired_root.findall(".//xe:GigabitEthernet", NS)

    for port in ports:
        iface = port.find("xe:name", NS).text
        vlan = port.find(".//xe:access/xe:vlan", NS).text

        filter_body = f"""
        <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
          <interface>
            <GigabitEthernet>
              <name>{iface}</name>
              <switchport>
                <access>
                  <vlan/>
                </access>
              </switchport>
            </GigabitEthernet>
          </interface>
        </native>
        """

        running_xml = netconf_get_config(m, filter_body)

        xpath_vlan = f".//xe:GigabitEthernet[xe:name='{iface}']/xe:switchport/xe:access/xe:vlan"
        current_vlan = extract_text(running_xml, xpath_vlan)

        print(f"      Interface Gi{iface} current VLAN: {current_vlan}")
        print(f"      Interface Gi{iface} desired VLAN: {vlan}")

        if current_vlan != vlan:
            return True

    return False


CHECK_FUNCTIONS = {
    "01_hostname.xml": check_hostname,
    "02_int_desc.xml": check_interface_desc,
    "03_int_ip.xml": check_interface_ip,
    "04_ospf.xml": check_ospf,
    "02_vlan50.xml": check_vlan,
    "03_svi_vlan50.xml": check_svi_vlan_ip,
    "04_default_gateway.xml": check_default_gateway,
    "05_access_ports.xml": check_switchport_access_vlan
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