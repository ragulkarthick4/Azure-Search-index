#!/usr/bin/env python3
"""
Azure Test Report Processor - Final Version
"""

import json
from bs4 import BeautifulSoup
from datetime import datetime
import uuid
import re
from azure.identity import ClientSecretCredential
from azure.core.credentials import AzureKeyCredential
from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.search import SearchManagementClient
from azure.storage.fileshare import ShareServiceClient
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.core.exceptions import AzureError, ResourceExistsError

# Configuration
CONFIG = {
    "SUBSCRIPTION_ID": "fcf78033-3ec8-4642-8ea2-78e14f07e5e3",
    "RESOURCE_GROUP": "fileprocess-rg1",
    "LOCATION": "eastus",
    "STORAGE": {
        "ACCOUNT_NAME": "fileprocessorsk01",
        "FILE_SHARE_NAME": "file-share",
        "LOCAL_HTML_FILE": "Oracle_SQL_PLSQL_TOP.html",
        "UPLOAD_PATH": "reports/Oracle_SQL_PLSQL_TOP.html"
    },
    "SEARCH": {
        "SERVICE_NAME": "fileprocesssearch",
        "INDEX_NAME": "reports-index"
    },
    "METADATA": {
        "PROCESSOR_VERSION": "2.0.3",
        "CURRENT_USER": "sumi9876",
        "PROCESSING_TIME": "2025-07-30 21:01:11"
    },
    "CREDENTIALS": {
        "TENANT_ID": "58541453-4e85-4f05-9032-7b95cb17fd33",
        "CLIENT_ID": "5f8b60b9-7c43-482a-875e-603e8e3a7b91",
        "CLIENT_SECRET": "*************"
    }
}

def clean_version_string(version_str):
    if not version_str:
        return ""
    version_str = re.sub(r'marker\\n|["\']', '', version_str)
    if ':' in version_str:
        version_str = version_str.split(':', 1)[1].strip()
    return version_str.strip()

def clean_json_string(json_str):
    try:
        json_str = json_str.strip().strip('"')
        json_str = json_str.replace('\\"', '"')
        json_str = json_str.replace('\\n', ' ')
        json_str = json_str.replace('\\t', ' ')
        json_str = re.sub(r'([{,]\s*)([a-zA-Z0-9_\-]+)\s*:', r'\1"\2":', json_str)
        json_str = re.sub(r':\s*"([^"]*)"([},])', r':"\1"\2', json_str)
        json_str = json_str.replace("'", '"')
        return json_str
    except Exception:
        return json_str

class TestReportProcessor:
    def __init__(self, config):
        self.config = config
        self.credential = ClientSecretCredential(
            tenant_id=config["CREDENTIALS"]["TENANT_ID"],
            client_id=config["CREDENTIALS"]["CLIENT_ID"],
            client_secret=config["CREDENTIALS"]["CLIENT_SECRET"]
        )
        self.processing_timestamp = datetime.strptime(
            config["METADATA"]["PROCESSING_TIME"], 
            "%Y-%m-%d %H:%M:%S"
        )
        self.setup_clients()

    def setup_clients(self):
        self.storage_mgmt_client = StorageManagementClient(
            credential=self.credential,
            subscription_id=self.config["SUBSCRIPTION_ID"]
        )
        
        keys = self.storage_mgmt_client.storage_accounts.list_keys(
            self.config["RESOURCE_GROUP"],
            self.config["STORAGE"]["ACCOUNT_NAME"]
        )
        storage_key = keys.keys[0].value
        
        self.file_share_client = ShareServiceClient(
            account_url=f"https://{self.config['STORAGE']['ACCOUNT_NAME']}.file.core.windows.net",
            credential=storage_key
        )
        
        search_mgmt_client = SearchManagementClient(
            credential=self.credential,
            subscription_id=self.config["SUBSCRIPTION_ID"]
        )
        
        admin_key = search_mgmt_client.admin_keys.get(
            self.config["RESOURCE_GROUP"],
            self.config["SEARCH"]["SERVICE_NAME"]
        ).primary_key
        
        search_endpoint = f"https://{self.config['SEARCH']['SERVICE_NAME']}.search.windows.net"
        credential = AzureKeyCredential(admin_key)
        
        self.search_client = SearchClient(
            endpoint=search_endpoint,
            index_name=self.config["SEARCH"]["INDEX_NAME"],
            credential=credential
        )

    def setup_file_share(self):
        share_client = self.file_share_client.get_share_client(
            self.config["STORAGE"]["FILE_SHARE_NAME"]
        )
        
        dir_path = '/'.join(self.config["STORAGE"]["UPLOAD_PATH"].split('/')[:-1])
        if dir_path:
            dir_client = share_client.get_directory_client(dir_path)
            try:
                dir_client.create_directory()
            except ResourceExistsError:
                pass
        
        file_client = share_client.get_file_client(self.config["STORAGE"]["UPLOAD_PATH"])
        with open(self.config["STORAGE"]["LOCAL_HTML_FILE"], "rb") as data:
            file_client.upload_file(data)
        
        return share_client

    def extract_environment_from_html(self, soup):
        env = {
            "Python": "",
            "Platform": "",
            "Packages": {
                "pytest": "",
                "pluggy": ""
            },
            "plugins": {
                "base-url": "",
                "playwright": "",
                "asyncio": "",
                "html": "",
                "metadata": ""
            },
            "PLATFORM": "",
            "Base URL": ""
        }
        
        env_table = soup.find('table', id='environment')
        if not env_table:
            return env
            
        for row in env_table.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) < 2:
                continue
                
            key = cells[0].get_text().strip()
            value_cell = cells[1]
            
            if key == "Packages":
                for li in value_cell.find_all('li'):
                    text = li.get_text().strip()
                    if 'pytest:' in text:
                        env["Packages"]["pytest"] = clean_version_string(text)
                    elif 'pluggy:' in text:
                        env["Packages"]["pluggy"] = clean_version_string(text)
            
            elif key == "Plugins":
                for li in value_cell.find_all('li'):
                    text = li.get_text().strip()
                    if 'base-url:' in text:
                        env["plugins"]["base-url"] = clean_version_string(text)
                    elif 'playwright:' in text:
                        env["plugins"]["playwright"] = clean_version_string(text)
                    elif 'asyncio:' in text:
                        env["plugins"]["asyncio"] = clean_version_string(text)
                    elif 'html:' in text:
                        env["plugins"]["html"] = clean_version_string(text)
                    elif 'metadata:' in text:
                        env["plugins"]["metadata"] = clean_version_string(text)
            
            elif key == "Python":
                env["Python"] = value_cell.get_text().strip()
            elif key == "Platform":
                env["Platform"] = value_cell.get_text().strip()
            elif key == "PLATFORM":
                env["PLATFORM"] = value_cell.get_text().strip()
            elif key == "Base URL":
                env["Base URL"] = value_cell.get_text().strip()
        
        return env

    def extract_test_data(self, share_client):
        file_client = share_client.get_file_client(self.config["STORAGE"]["UPLOAD_PATH"])
        download = file_client.download_file()
        html_content = download.readall().decode('utf-8')
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        environment = {
            "python": "",
            "platform": "",
            "packages": {
                "pytest": "",
                "pluggy": ""
            },
            "plugins": {
                "base_url": "",
                "playwright": "",
                "asyncio": "",
                "html": "",
                "metadata": ""
            },
            "platform_type": "",
            "base_url": ""
        }
        
        data_container = soup.find('div', id='data-container')
        if data_container and 'data-jsonblob' in data_container.attrs:
            try:
                json_str = data_container['data-jsonblob']
                json_str = clean_json_string(json_str)
                json_data = json.loads(json_str)
                
                if 'environment' in json_data:
                    json_env = json_data['environment']
                    
                    environment.update({
                        "python": json_env.get("Python", ""),
                        "platform": json_env.get("Platform", ""),
                        "platform_type": json_env.get("PLATFORM", ""),
                        "base_url": json_env.get("Base URL", "")
                    })
                    
                    if 'Packages' in json_env:
                        pkg_data = json_env["Packages"]
                        environment["packages"].update({
                            "pytest": clean_version_string(str(pkg_data.get("pytest", ""))),
                            "pluggy": clean_version_string(str(pkg_data.get("pluggy", "")))
                        })
                    
                    if 'plugins' in json_env:
                        plugin_data = json_env["plugins"]
                        environment["plugins"].update({
                            "base_url": clean_version_string(str(plugin_data.get("base-url", ""))),
                            "playwright": clean_version_string(str(plugin_data.get("playwright", ""))),
                            "asyncio": clean_version_string(str(plugin_data.get("asyncio", ""))),
                            "html": clean_version_string(str(plugin_data.get("html", ""))),
                            "metadata": clean_version_string(str(plugin_data.get("metadata", "")))
                        })
                        
            except json.JSONDecodeError:
                html_env = self.extract_environment_from_html(soup)
                
                environment.update({
                    "python": html_env.get("Python", ""),
                    "platform": html_env.get("Platform", ""),
                    "platform_type": html_env.get("PLATFORM", ""),
                    "base_url": html_env.get("Base URL", "")
                })
                
                if 'Packages' in html_env:
                    pkg_data = html_env["Packages"]
                    environment["packages"].update({
                        "pytest": clean_version_string(str(pkg_data.get("pytest", ""))),
                        "pluggy": clean_version_string(str(pkg_data.get("pluggy", "")))
                    })
                
                if 'plugins' in html_env:
                    plugin_data = html_env["plugins"]
                    environment["plugins"].update({
                        "base_url": clean_version_string(str(plugin_data.get("base-url", ""))),
                        "playwright": clean_version_string(str(plugin_data.get("playwright", ""))),
                        "asyncio": clean_version_string(str(plugin_data.get("asyncio", ""))),
                        "html": clean_version_string(str(plugin_data.get("html", ""))),
                        "metadata": clean_version_string(str(plugin_data.get("metadata", "")))
                    })
        
        tests = []
        results_table = soup.find('table', id='results-table')
        if results_table:
            for tbody in results_table.find_all('tbody', class_='results-table-row'):
                test_row = tbody.find('tr', class_='collapsible')
                if test_row:
                    result_cell = test_row.find('td', class_='col-result')
                    duration_cell = test_row.find('td', class_='col-duration')
                    
                    if all([result_cell, duration_cell]):
                        test_data = {
                            "result": result_cell.text.strip(),
                            "duration": duration_cell.text.strip()
                        }
                        tests.append(test_data)
        
        title = soup.find('h1', id='title')
        title = title.text.strip() if title else "report.html"
        
        return {
            "environment": environment,
            "tests": tests,
            "title": title
        }

    def index_test_results(self, test_data):
        documents = []
        env = test_data.get('environment', {})
        
        for test in test_data.get('tests', []):
            pytest_version = clean_version_string(str(env.get('packages', {}).get('pytest', '')))
            pluggy_version = clean_version_string(str(env.get('packages', {}).get('pluggy', '')))
            base_url_version = clean_version_string(str(env.get('plugins', {}).get('base_url', '')))
            playwright_version = clean_version_string(str(env.get('plugins', {}).get('playwright', '')))
            asyncio_version = clean_version_string(str(env.get('plugins', {}).get('asyncio', '')))
            html_version = clean_version_string(str(env.get('plugins', {}).get('html', '')))
            metadata_version = clean_version_string(str(env.get('plugins', {}).get('metadata', '')))
            
            packages = [
                f"pytest:{pytest_version}",
                f"pluggy:{pluggy_version}"
            ]
            
            plugins = [
                f"base_url:{base_url_version}",
                f"playwright:{playwright_version}",
                f"asyncio:{asyncio_version}",
                f"html:{html_version}",
                f"metadata:{metadata_version}"
            ]
            
            doc = {
                "id": str(uuid.uuid4()),
                "timestamp": self.processing_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "python_version": str(env.get('python', '')),
                "platform": str(env.get('platform', '')),
                "packages": packages,
                "plugins": plugins,
                "playwright_platform": str(env.get('platform_type', '')),
                "duration": test.get('duration', '')
            }
            print(doc["id"])
            documents.append(doc)
        
        if documents:
            self.search_client.upload_documents(documents)

    def execute(self):
        share = self.setup_file_share()
        test_data = self.extract_test_data(share)
        self.index_test_results(test_data)

if __name__ == "__main__":
    processor = TestReportProcessor(CONFIG)
    processor.execute()
