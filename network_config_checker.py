import json
import subprocess
import socket
import time
from datetime import datetime
from pathlib import Path
from jsonschema import Draft202012Validator

# Files are stored beside this Python script.
HERE = Path(__file__).resolve().parent
TOP = "student-host:host"


def powershell(command):
    # Run a Windows command and receive structured JSON.
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            command
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True
    )

    output = result.stdout.strip()
    return json.loads(output) if output else []


def collect_configuration():
    # Collect multiple Windows network data sources.
    adapters = powershell(
        "ConvertTo-Json -InputObject @("
        "Get-NetAdapter -IncludeHidden | "
        "Select-Object Name, InterfaceDescription, "
        "ifIndex, Status, MacAddress"
        ") -Depth 4"
    )

    addresses = powershell(
        "ConvertTo-Json -InputObject @("
        "Get-NetIPAddress | "
        "Select-Object InterfaceIndex, InterfaceAlias, "
        "IPAddress, PrefixLength"
        ") -Depth 4"
    )

    ip_interfaces = powershell(
        "ConvertTo-Json -InputObject @("
        "Get-NetIPInterface | "
        "Select-Object InterfaceIndex, InterfaceAlias, "
        "NlMtu, InterfaceMetric, "
        "@{Name='Family';Expression={$_.AddressFamily.ToString()}}, "
        "@{Name='State';Expression={$_.ConnectionState.ToString()}}"
        ") -Depth 4"
    )

    routes = powershell(
        "ConvertTo-Json -InputObject @("
        "Get-NetRoute -AddressFamily IPv4 | "
        "Where-Object {$_.DestinationPrefix -eq '0.0.0.0/0'} | "
        "Select-Object InterfaceIndex, NextHop, RouteMetric"
        ") -Depth 4"
    )

    dns = powershell(
        "ConvertTo-Json -InputObject @("
        "Get-DnsClientServerAddress | "
        "Select-Object InterfaceIndex, ServerAddresses"
        ") -Depth 4"
    )

    host = {
        "hostname": socket.gethostname(),
        "os": "windows",
        "collected-at": datetime.now().astimezone().isoformat(),
        "interface": []
    }

    # Match records using interface index, not list order.
    adapter_map = {
        a["ifIndex"]: a for a in adapters
    }

    address_map = {}
    for address in addresses:
        index = address["InterfaceIndex"]
        address_map.setdefault(index, []).append(address)

    ip_map = {}
    for row in ip_interfaces:
        index = row["InterfaceIndex"]
        ip_map.setdefault(index, []).append(row)

    # Include interfaces missing from Get-NetAdapter.
    all_indexes = (
        set(adapter_map)
        | set(address_map)
        | set(ip_map)
    )

    for index in sorted(all_indexes):
        adapter = adapter_map.get(index, {})
        ip_rows = ip_map.get(index, [])
        addr_rows = address_map.get(index, [])

        name = adapter.get("Name")

        if not name and ip_rows:
            name = ip_rows[0]["InterfaceAlias"]

        if not name and addr_rows:
            name = addr_rows[0]["InterfaceAlias"]

        if not name:
            continue

        description = (
            name + " " +
            str(adapter.get("InterfaceDescription") or "")
        ).lower()

        # Classify physical and virtual interfaces.
        if "loopback" in description:
            interface_type = "loopback"

        elif any(word in description for word in [
            "virtual", "vethernet", "nordlynx",
            "vpn", "hyper-v", "tap-", "tunnel"
        ]):
            interface_type = "virtual"

        elif "wi-fi" in description or "wireless" in description:
            interface_type = "wifi"

        elif "bluetooth" in description:
            interface_type = "other"

        elif "ethernet" in description:
            interface_type = "ethernet"

        else:
            interface_type = "other"

        # Convert Windows status into the model's up/down values.
        status = str(adapter.get("Status") or "").lower()

        if status:
            is_up = status == "up"
        else:
            is_up = any(
                str(row["State"]).lower() == "connected"
                for row in ip_rows
            )

        interface = {
            "name": name,
            "type": interface_type,
            "oper-status": "up" if is_up else "down",
            "ipv4": [],
            "ipv6": []
        }

        # Convert MAC addresses to lowercase colon format.
        mac = adapter.get("MacAddress")

        if mac:
            normalized = mac.lower().replace("-", ":")
            interface["mac"] = normalized

        # Prefer IPv4 MTU because the schema has one MTU field.
        selected = next(
            (row for row in ip_rows
             if row["Family"] == "IPv4"),
            ip_rows[0] if ip_rows else None
        )

        if selected:
            mtu = selected.get("NlMtu")

            # Windows loopback can exceed the course model's limit.
            # Keep the interface but omit an unrepresentable MTU.
            if isinstance(mtu, int) and 68 <= mtu <= 65536:
                interface["mtu"] = mtu
            else:
                print(
                    f"NOTE: {name}: MTU {mtu} "
                    "is outside the course schema range."
                )

        # Include prefix lengths and remove IPv6 scope identifiers.
        for row in addr_rows:
            ip = row["IPAddress"].split("%")[0]
            prefix = row["PrefixLength"]

            family = "ipv6" if ":" in ip else "ipv4"
            value = f"{ip}/{prefix}"

            if value not in interface[family]:
                interface[family].append(value)

        host["interface"].append(interface)

    # The model stores one default gateway.
    # Prefer routes with the lowest route + interface metric.
    metrics = {
        row["InterfaceIndex"]: row["InterfaceMetric"]
        for row in ip_interfaces
        if row["Family"] == "IPv4"
    }

    usable_routes = [
        route for route in routes
        if route.get("NextHop") not in (None, "", "0.0.0.0")
    ]

    if usable_routes:
        best_route = min(
            usable_routes,
            key=lambda route: (
                route["RouteMetric"]
                + metrics.get(route["InterfaceIndex"], 0)
            )
        )

        host["default-gateway"] = best_route["NextHop"]

        # Use DNS servers belonging to the selected route's interface.
        main_index = best_route["InterfaceIndex"]

        host["dns-server"] = list(dict.fromkeys(
            server
            for row in dns
            if row["InterfaceIndex"] == main_index
            for server in (row.get("ServerAddresses") or [])
        ))

    else:
        # A disconnected computer might not have a default route.
        host["dns-server"] = []
        print("NOTE: No IPv4 default gateway found.")

    return {TOP: host}


def validate_configuration(observed):
    # Validate actual collected data using the professor's schema.
    schema = json.loads(
        (HERE / "host_schema.json").read_text(encoding="utf-8")
    )

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(observed))

    print("\n=== SCHEMA VALIDATION ===")

    if not errors:
        print("VALID: observed.json matches host_schema.json")
    else:
        print(f"INVALID: {len(errors)} error(s)")

        for error in errors:
            path = "/".join(
                str(part) for part in error.absolute_path
            )

            print(f"ERROR at {path}: {error.message}")

    return errors


def report_drift(intended, observed):
    # Compare only the fields specified in intended.json.
    expected = intended[TOP]
    actual = observed[TOP]
    differences = []

    for field, value in expected.items():
        if field == "interface":
            continue

        observed_value = actual.get(field, "<missing>")

        if value != observed_value:
            differences.append(
                f"{field}: intended={value}, "
                f"observed={observed_value}"
            )

    # Match interfaces by name, not by their position in JSON.
    actual_interfaces = {
        interface["name"]: interface
        for interface in actual.get("interface", [])
    }

    for expected_interface in expected.get("interface", []):
        name = expected_interface["name"]
        actual_interface = actual_interfaces.get(name)

        if actual_interface is None:
            differences.append(
                f"interface {name}: intended=present, "
                "observed=missing"
            )
            continue

        for field, value in expected_interface.items():
            if field == "name":
                continue

            observed_value = actual_interface.get(
                field, "<missing>"
            )

            if value != observed_value:
                differences.append(
                    f"interface {name}/{field}: "
                    f"intended={value}, "
                    f"observed={observed_value}"
                )

    print("\n=== CONFIGURATION DRIFT ===")

    if differences:
        for difference in differences:
            print(difference)
    else:
        print("No drift detected.")

    print("Total differences:", len(differences))

    return differences


def main():
    start = time.perf_counter()

    observed = collect_configuration()

    (HERE / "observed.json").write_text(
        json.dumps(observed, indent=4),
        encoding="utf-8"
    )

    elapsed = time.perf_counter() - start

    print("\n=== COLLECTION RESULTS ===")
    print("Observed configuration saved to observed.json")
    print("Hostname:", observed[TOP]["hostname"])
    print("Interfaces:", len(observed[TOP]["interface"]))
    print("Collection time:", round(elapsed, 3), "seconds")

    errors = validate_configuration(observed)

    intended = json.loads(
        (HERE / "intended.json").read_text(encoding="utf-8")
    )

    differences = report_drift(intended, observed)

    summary = {
        "collection_seconds": elapsed,
        "interface_count": len(observed[TOP]["interface"]),
        "schema_error_count": len(errors),
        "drift_count": len(differences),
        "differences": differences
    }

    (HERE / "results.json").write_text(
        json.dumps(summary, indent=4),
        encoding="utf-8"
    )

    return observed, summary


if __name__ == "__main__":
    main()
