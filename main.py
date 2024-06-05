import json
import subprocess
import distro
import re
import argparse
import os
import shutil
import datetime
import zipfile
import sys

def load_psmp_versions_json(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)

def get_installed_psmp_version():
    try:
        result = subprocess.check_output("rpm -qa | grep -i cark", shell=True, universal_newlines=True).strip()
        if result:
            # Extract version number, assuming the result format is "CARKpsmp-14.0.0-14.x86_64"
            version = result.split('-')[1]
            # Extract major and minor version numbers
            major, minor, _ = version.split('.', 2)
            main_version = f"{major}.{minor}"
            return main_version
    except subprocess.CalledProcessError:
        return None

def get_linux_distribution():
    version_info = distro.version(best=True)
    version_parts = version_info.split('.')
    major = version_parts[0]
    minor = version_parts[1] if len(version_parts) > 1 else '0'
    main_version = f"{major}.{minor}"
    return distro.name(), main_version


def is_supported(psmp_versions, psmp_version, distro_name, distro_version):
    if psmp_version not in psmp_versions:
        return False
    for version in psmp_versions:
        if version.startswith(psmp_version):  # Check if PSMP version starts with given major and minor version
            for distro_info in psmp_versions[version]['supported_distributions']:
                if distro_info['name'].lower() == distro_name.lower():
                    # Check if the distro version matches any of the supported versions
                    for supported_version in distro_info.get('versions', []):
                        if distro_version.startswith(supported_version):
                            return True
    return False


def check_services_status():
    service_statuses = {}
    
    # Check PSMP service status
    try:
        result_psmpsrv = subprocess.check_output("systemctl status psmpsrv", shell=True, universal_newlines=True)
        if "Active: active" in result_psmpsrv:
            with open("/var/opt/CARKpsmp/logs/PSMPConsole.log", "r") as log_file:
                log_content = log_file.read()
                if "is up and working with Vault" in log_content:
                    service_statuses["psmpsrv"] = "Running and communicating with Vault"
                else:
                    service_statuses["psmpsrv"] = "Running but not communicating with Vault"
        elif "Active: inactive" in result_psmpsrv:
            service_statuses["psmpsrv"] = "Inactive"
        else:
            service_statuses["psmpsrv"] = "Inactive"
    except subprocess.CalledProcessError:
        service_statuses["psmpsrv"] = "Inactive"

    # Check SSHD service status
    try:
        result_sshd = subprocess.check_output("systemctl status sshd", shell=True, universal_newlines=True)
        if "Active: active" in result_sshd:
            service_statuses["sshd"] = "Running"
        elif "Active: inactive" in result_sshd:
            service_statuses["sshd"] = "Inactive"
        else:
            service_statuses["sshd"] = "Inactive"
    except subprocess.CalledProcessError:
        service_statuses["sshd"] = "Inactive"
    
    return service_statuses

def get_openssh_version():
    try:
        # Get the version of OpenSSH installed
        ssh_version_output = subprocess.check_output(["ssh", "-V"], stderr=subprocess.STDOUT, universal_newlines=True)
        ssh_version_match = re.search(r"OpenSSH_(\d+\.\d+)", ssh_version_output)
        if ssh_version_match:
            ssh_version = float(ssh_version_match.group(1))
            return ssh_version
        else:
            return None
    except subprocess.CalledProcessError as e:
        return None


def check_openssh_version():
    try:
        # Get the version of OpenSSH installed
        ssh_version = get_openssh_version()
        if ssh_version is not None:
            if ssh_version >= 7.7:
                return True, "", ssh_version
            else:
                return False, f"[+] OpenSSH version is: {ssh_version}, required version 7.7 and above.", ssh_version
        else:
            return False, "Failed to determine OpenSSH version.", None
    except subprocess.CalledProcessError as e:
        return False, f"Error: {e}", None



def check_sshd_config():
    sshd_config_path = "/etc/ssh/sshd_config"  # Modify this path as needed
    found_pmsp_auth_block = False
    found_allow_user = False
    found_pubkey_accepted_algorithms = False
    
    try:
        with open(sshd_config_path, "r") as file:
            for line in file:
                # Check for PSMP Authentication Configuration Block Start
                if line.strip() == "# PSMP Authentication Configuration Block Start":
                    found_pmsp_auth_block = True
                # Check for AllowUser line
                if line.strip().startswith("AllowUser"):
                    found_allow_user = True
                # Check if the line contains PubkeyAcceptedAlgorithms and is uncommented
                if "PubkeyAcceptedAlgorithms" in line and not line.strip().startswith("#"):
                    found_pubkey_accepted_algorithms = True
    except FileNotFoundError:
        print("sshd_config file not found.")
        return
    
    if not found_pmsp_auth_block:
        print("PSMP authentication block not found.")
    if found_allow_user:
        print("AllowUser mentioned found.")
    else:
        print("[+] SSH-Key auth not enabled, sshd_config missing 'PubkeyAcceptedAlgorithms'.")

def logs_collect():
    # Define folders to copy logs from
    log_folders = [
        "/var/log/secure",
        "/var/log/messages",
        "/var/opt/CARKpsmp/logs",
        "/var/opt/CARKpsmp/logs/components",
        "/etc/ssh/sshd_config",
        "/etc/pam.d/sshd",
        "/etc/pam.d/password-auth",
        "/etc/pam.d/system-auth",
        "/etc/nsswitch.conf",
        "/var/opt/CARKpsmp/temp/EnvManager.log"
    ]

    # Create a folder for temporary storage
    temp_folder = "/tmp/psmp_logs"
    os.makedirs(temp_folder, exist_ok=True)

    try:
        # Copy logs from each folder to the temporary folder
        for folder in log_folders:
            if os.path.exists(folder):
                if os.path.isdir(folder):
                    shutil.copytree(folder, os.path.join(temp_folder, os.path.basename(folder)))
                else:
                    shutil.copy(folder, temp_folder)
            else:
                print(f"Folder not found: {folder}")

        # Get the current date in the format DD.MM.YY
        current_date = datetime.datetime.now().strftime("%m.%d.%y")

        # Create a zip file with the specified name format
        zip_filename = f"PSMP_Logs_{current_date}.zip"
        with zipfile.ZipFile(zip_filename, "w") as zipf:
            for root, dirs, files in os.walk(temp_folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    zipf.write(file_path, os.path.relpath(file_path, temp_folder))

        print(f"Logs copied and zip file created: {zip_filename}")

    except Exception as e:
        print(f"An error occurred: {e}")

    finally:
        # Clean up temporary folder
        shutil.rmtree(temp_folder, ignore_errors=True)
if __name__ == "__main__":
    # Check if the command-line argument is 'logs', then execute the function
    if len(sys.argv) == 2 and sys.argv[1] == "logs":
        logs_collect()
        sys.exit(1)  # Exit after collecting logs

    # Load PSMP versions from a JSON file
    psmp_versions = load_psmp_versions_json('src/versions.json')

    # Get the installed PSMP version
    psmp_version = get_installed_psmp_version()
    if not psmp_version:
        print("[+] No PSMP version found.")
        sys.exit(1)

    # Get the Linux distribution and version
    distro_name, distro_version = get_linux_distribution()

    print(f"PSMP version: {psmp_version}")
    print(f"Linux distribution: {distro_name} {distro_version}")

    # Check compatibility
    if is_supported(psmp_versions, psmp_version, distro_name, distro_version):
        print(f"PSMP version {psmp_version} Supports {distro_name} {distro_version}")
    else:
        print(f"PSMP version {psmp_version} Does Not Support {distro_name} {distro_version}")

    # Check service status
    service_status = check_services_status()
    print(f"PSMP Service Status: {service_status.get('psmpsrv', 'Unavailable')}")
    print(f"SSHD Service Status: {service_status.get('sshd', 'Unavailable')}")

    success, message, ssh_version = check_openssh_version()
    if not success:
        print(message)

    check_sshd_config()
