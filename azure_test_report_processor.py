#!/usr/bin/env python3
"""
Azure Test Report Processor - Final Clean Version
"""

import json
from bs4 import BeautifulSoup
from datetime import datetime
import uuid
import time
import re
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.search import SearchManagementClient
from azure.storage.fileshare import ShareServiceClient
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchFieldDataType,
    SearchableField,
    ComplexField
)
from azure.core.exceptions import AzureError, ResourceExistsError
from azure.mgmt.search.models import SearchService, Sku

# Configuration
CONFIG = {
    "SUBSCRIPTION_ID": "fcf78033-3ec8-4642-8ea2-78e14f07e5e3",
    "RESOURCE_GROUP": "fileprocess-rg1",
    "LOCATION": "eastus",
    "STORAGE": {
        "ACCOUNT_NAME": "fileprocessorsk01",
        "FILE_SHARE_NAME": "file-share",
        "LOCAL_HTML_FILE": "sample2 -13.html",
        "UPLOAD_PATH": "reports/Oracle_SQL_PLSQL_TOP.html"
    },
    "SEARCH": {
        "SERVICE_NAME": "fileprocesssearch",
        "INDEX_NAME": "testindex"
    },
    "METADATA": {
        "PROCESSOR_VERSION": "2.0.3",
        "CURRENT_USER": "sumi9876",
        "PROCESSING_TIME": "2025-07-30 21:01:11"
    }
}

def clean_version_string(version_str):
    """Clean version strings by removing unwanted markers and extracting just the version"""
    if not version_str:
        return ""
    
    # Remove all 'marker\n' occurrences and quotes
    version_str = re.sub(r'marker\\n|["\']', '', version_str)
    
    # If there's a colon, take the part after it
    if ':' in version_str:
        version_str = version_str.split(':', 1)[1].strip()
    
    # Remove any remaining whitespace
    return version_str.strip()

def clean_json_string(json_str):
    """Clean and prepare JSON string for parsing"""
    try:
        # Remove leading/trailing whitespace and quotes
        json_str = json_str.strip().strip('"')
        
        # Handle escaped quotes and special characters
        json_str = json_str.replace('\\"', '"')
        json_str = json_str.replace('\\n', ' ')
        json_str = json_str.replace('\\t', ' ')
        
        # Fix common JSON formatting issues
        json_str = re.sub(r'([{,]\s*)([a-zA-Z0-9_\-]+)\s*:', r'\1"\2":', json_str)
        json_str = re.sub(r':\s*"([^"]*)"([},])', r':"\1"\2', json_str)
        
        # Clean up any remaining issues
        json_str = json_str.replace("'", '"')
        
        return json_str
    except Exception as e:
        print(f"Error cleaning JSON string: {e}")
        return json_str

class TestReportProcessor:
    def __init__(self, config):
        self.config = config
        self.credential = DefaultAzureCredential()
        self.processing_timestamp = datetime.strptime(
            config["METADATA"]["PROCESSING_TIME"], 
            "%Y-%m-%d %H:%M:%S"
        )
        self.setup_clients()

    def setup_clients(self):
        """Initialize Azure clients"""
        try:
            # Storage Management Client
            self.storage_mgmt_client = StorageManagementClient(
                credential=self.credential,
                subscription_id=self.config["SUBSCRIPTION_ID"]
            )
            
            # Get storage key for file operations
            keys = self.storage_mgmt_client.storage_accounts.list_keys(
                self.config["RESOURCE_GROUP"],
                self.config["STORAGE"]["ACCOUNT_NAME"]
            )
            storage_key = keys.keys[0].value
            
            # File Share Client
            self.file_share_client = ShareServiceClient(
                account_url=f"https://{self.config['STORAGE']['ACCOUNT_NAME']}.file.core.windows.net",
                credential=storage_key
            )
            
            # Search clients will be initialized later
            self.search_client = None
            self.index_client = None
            
        except AzureError as e:
            print(f"Client initialization failed: {str(e)}")
            raise

    def ensure_search_service_exists(self):
        """Ensure Azure Search service exists and is properly configured"""
        try:
            # Create Search Management Client
            search_mgmt_client = SearchManagementClient(
                credential=self.credential,
                subscription_id=self.config["SUBSCRIPTION_ID"]
            )
            
            search_service_name = self.config["SEARCH"]["SERVICE_NAME"]
            max_retries = 5
            base_delay = 30  # seconds
            
            for attempt in range(max_retries):
                try:
                    # Check if service exists
                    try:
                        search_service = search_mgmt_client.services.get(
                            self.config["RESOURCE_GROUP"],
                            search_service_name
                        )
                        print(f"Search service {search_service_name} already exists")
                        break
                    except Exception:
                        print(f"Creating search service {search_service_name}...")
                        delay = base_delay * (2 ** attempt)  # Exponential backoff
                        print(f"Attempt {attempt + 1}/{max_retries}, waiting {delay} seconds...")
                        time.sleep(delay)
                        
                        poller = search_mgmt_client.services.begin_create_or_update(
                            resource_group_name=self.config["RESOURCE_GROUP"],
                            search_service_name=search_service_name,
                            service=SearchService(
                                location=self.config["LOCATION"],
                                sku=Sku(name='basic')
                            )
                        )
                        search_service = poller.result()
                        print(f"Search service {search_service_name} created successfully")
                        time.sleep(30)  # Wait for service to be fully provisioned
                        break
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    print(f"Attempt {attempt + 1} failed: {str(e)}")
                    continue
            
            # Get the admin key
            admin_key = search_mgmt_client.admin_keys.get(
                self.config["RESOURCE_GROUP"],
                search_service_name
            ).primary_key
            
            # Initialize the search clients with AzureKeyCredential
            search_endpoint = f"https://{search_service_name}.search.windows.net"
            credential = AzureKeyCredential(admin_key)
            
            self.search_client = SearchClient(
                endpoint=search_endpoint,
                index_name=self.config["SEARCH"]["INDEX_NAME"],
                credential=credential
            )
            self.index_client = SearchIndexClient(
                endpoint=search_endpoint,
                credential=credential
            )
            
            print("Search service configured successfully")
            
        except Exception as e:
            print(f"Error ensuring search service exists: {str(e)}")
            raise

    def setup_file_share(self):
        """Setup file share and upload test report"""
        try:
            # Get existing file share
            share_client = self.file_share_client.get_share_client(
                self.config["STORAGE"]["FILE_SHARE_NAME"]
            )
            
            # Create directory structure if it doesn't exist
            dir_path = '/'.join(self.config["STORAGE"]["UPLOAD_PATH"].split('/')[:-1])
            if dir_path:
                dir_client = share_client.get_directory_client(dir_path)
                try:
                    dir_client.create_directory()
                    print(f"Created directory: {dir_path}")
                except ResourceExistsError:
                    print("Directory already exists")
            
            # Upload file
            file_client = share_client.get_file_client(self.config["STORAGE"]["UPLOAD_PATH"])
            with open(self.config["STORAGE"]["LOCAL_HTML_FILE"], "rb") as data:
                file_client.upload_file(data)
            print("Test report uploaded successfully")
            
            return share_client
            
        except AzureError as e:
            print(f"File share setup failed: {str(e)}")
            raise

    def extract_environment_from_html(self, soup):
        """Extract environment data from HTML table with proper version cleaning"""
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
            
            # Handle packages
            if key == "Packages":
                for li in value_cell.find_all('li'):
                    text = li.get_text().strip()
                    if 'pytest:' in text:
                        env["Packages"]["pytest"] = clean_version_string(text)
                    elif 'pluggy:' in text:
                        env["Packages"]["pluggy"] = clean_version_string(text)
            
            # Handle plugins
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
            
            # Handle simple values
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
        """Extract test data from HTML report"""
        try:
            file_client = share_client.get_file_client(self.config["STORAGE"]["UPLOAD_PATH"])
            download = file_client.download_file()
            html_content = download.readall().decode('utf-8')
            
            soup = BeautifulSoup(html_content, 'html.parser')
            print("\nParsing HTML content...")
            
            # Initialize environment with default values
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
            
            # First try to get data from JSON blob
            data_container = soup.find('div', id='data-container')
            if data_container and 'data-jsonblob' in data_container.attrs:
                try:
                    json_str = data_container['data-jsonblob']
                    print("Original JSON string:", json_str)
                    
                    # Clean the JSON string
                    json_str = clean_json_string(json_str)
                    print("Cleaned JSON string:", json_str)
                    
                    # Parse the JSON
                    json_data = json.loads(json_str)
                    
                    if 'environment' in json_data:
                        json_env = json_data['environment']
                        print("Environment data from JSON:", json_env)
                        
                        # Update environment values
                        environment.update({
                            "python": json_env.get("Python", ""),
                            "platform": json_env.get("Platform", ""),
                            "platform_type": json_env.get("PLATFORM", ""),
                            "base_url": json_env.get("Base URL", "")
                        })
                        
                        # Update packages
                        if 'Packages' in json_env:
                            pkg_data = json_env["Packages"]
                            environment["packages"].update({
                                "pytest": clean_version_string(str(pkg_data.get("pytest", ""))),
                                "pluggy": clean_version_string(str(pkg_data.get("pluggy", "")))
                            })
                        
                        # Update plugins
                        if 'plugins' in json_env:
                            plugin_data = json_env["plugins"]
                            environment["plugins"].update({
                                "base_url": clean_version_string(str(plugin_data.get("base-url", ""))),
                                "playwright": clean_version_string(str(plugin_data.get("playwright", ""))),
                                "asyncio": clean_version_string(str(plugin_data.get("asyncio", ""))),
                                "html": clean_version_string(str(plugin_data.get("html", ""))),
                                "metadata": clean_version_string(str(plugin_data.get("metadata", "")))
                            })
                            
                except json.JSONDecodeError as e:
                    print(f"Warning: Could not parse JSON data from data-container: {e}")
                    print("Falling back to HTML parsing for environment data")
                    
                    # Fall back to HTML parsing if JSON parsing fails
                    html_env = self.extract_environment_from_html(soup)
                    print("Environment data from HTML:", html_env)
                    
                    environment.update({
                        "python": html_env.get("Python", ""),
                        "platform": html_env.get("Platform", ""),
                        "platform_type": html_env.get("PLATFORM", ""),
                        "base_url": html_env.get("Base URL", "")
                    })
                    
                    # Update packages
                    if 'Packages' in html_env:
                        pkg_data = html_env["Packages"]
                        environment["packages"].update({
                            "pytest": clean_version_string(str(pkg_data.get("pytest", ""))),
                            "pluggy": clean_version_string(str(pkg_data.get("pluggy", "")))
                        })
                    
                    # Update plugins
                    if 'plugins' in html_env:
                        plugin_data = html_env["plugins"]
                        environment["plugins"].update({
                            "base_url": clean_version_string(str(plugin_data.get("base-url", ""))),
                            "playwright": clean_version_string(str(plugin_data.get("playwright", ""))),
                            "asyncio": clean_version_string(str(plugin_data.get("asyncio", ""))),
                            "html": clean_version_string(str(plugin_data.get("html", ""))),
                            "metadata": clean_version_string(str(plugin_data.get("metadata", "")))
                        })
                except Exception as e:
                    print(f"Error processing environment data: {e}")
            
            # Parse test results
            tests = {}
            results_table = soup.find('table', id='results-table')
            if results_table:
                for tbody in results_table.find_all('tbody', class_='results-table-row'):
                    test_row = tbody.find('tr', class_='collapsible')
                    if test_row:
                        result_cell = test_row.find('td', class_='col-result')
                        test_id_cell = test_row.find('td', class_='col-testId')
                        duration_cell = test_row.find('td', class_='col-duration')
                        
                        if all([result_cell, test_id_cell, duration_cell]):
                            test_id = test_id_cell.text.strip()
                            
                            # Get log content
                            log_div = tbody.find('div', class_='log')
                            log_content = log_div.text.strip() if log_div else "No log output captured."
                            
                            # Get extras (media, etc.)
                            extras = []
                            extras_row = tbody.find('tr', class_='extras-row')
                            if extras_row:
                                media_div = extras_row.find('div', class_='media')
                                if media_div:
                                    img = media_div.find('img')
                                    if img and img.get('src'):
                                        extras.append({
                                            "name": "Screenshot",
                                            "format_type": "image",
                                            "content": img['src']
                                        })
                            
                            test_data = {
                                "extras": extras,
                                "result": result_cell.text.strip(),
                                "testId": test_id,
                                "duration": duration_cell.text.strip(),
                                "resultsTableRow": [
                                    str(result_cell.prettify()),
                                    str(test_id_cell.prettify()),
                                    str(duration_cell.prettify()),
                                    "<td class=\"col-links\"></td>"
                                ],
                                "log": log_content
                            }
                            
                            tests[test_id] = [test_data]
            
            # Get title
            title = soup.find('h1', id='title')
            title = title.text.strip() if title else "report.html"
            
            # Construct the final data structure
            test_data = {
                "environment": environment,
                "tests": tests,
                "renderCollapsed": ["passed"],
                "initialSort": "result",
                "title": title,
                "metadata": {
                    "processor_version": self.config["METADATA"]["PROCESSOR_VERSION"],
                    "processed_by": self.config["METADATA"]["CURRENT_USER"],
                    "processed_at": self.config["METADATA"]["PROCESSING_TIME"]
                }
            }
            
            print("Successfully parsed test data")
            print("Environment data:")
            print(json.dumps(environment, indent=2))
            return test_data
            
        except Exception as e:
            print(f"Error extracting test data: {str(e)}")
            raise

    def create_search_index(self):
        """Create or update search index with schema"""
        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="testId", type=SearchFieldDataType.String, filterable=True, sortable=True),
            SimpleField(name="result", type=SearchFieldDataType.String, filterable=True, sortable=True),
            SimpleField(name="duration", type=SearchFieldDataType.String, sortable=True),
            SearchableField(name="log", type=SearchFieldDataType.String),
            SimpleField(name="timestamp", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
            
            # Environment fields
            ComplexField(name="environment", fields=[
                SimpleField(name="python", type=SearchFieldDataType.String),
                SimpleField(name="platform", type=SearchFieldDataType.String),
                ComplexField(name="packages", fields=[
                    SimpleField(name="pytest", type=SearchFieldDataType.String),
                    SimpleField(name="pluggy", type=SearchFieldDataType.String)
                ]),
                ComplexField(name="plugins", fields=[
                    SimpleField(name="base_url", type=SearchFieldDataType.String),
                    SimpleField(name="playwright", type=SearchFieldDataType.String),
                    SimpleField(name="asyncio", type=SearchFieldDataType.String),
                    SimpleField(name="html", type=SearchFieldDataType.String),
                    SimpleField(name="metadata", type=SearchFieldDataType.String)
                ]),
                SimpleField(name="platform_type", type=SearchFieldDataType.String),
                SimpleField(name="base_url", type=SearchFieldDataType.String)
            ]),
            
            # Metadata fields
            ComplexField(name="metadata", fields=[
                SimpleField(name="processor_version", type=SearchFieldDataType.String),
                SimpleField(name="processed_by", type=SearchFieldDataType.String),
                SimpleField(name="processed_at", type=SearchFieldDataType.DateTimeOffset)
            ]),
            
            SimpleField(name="title", type=SearchFieldDataType.String)
        ]

        index = SearchIndex(
            name=self.config["SEARCH"]["INDEX_NAME"],
            fields=fields
        )

        try:
            # Check if index exists
            try:
                existing_index = self.index_client.get_index(self.config["SEARCH"]["INDEX_NAME"])
                print(f"Search index '{self.config['SEARCH']['INDEX_NAME']}' already exists")
            except Exception:
                # Index doesn't exist, create it
                self.index_client.create_index(index)
                print("Search index created successfully")
        except AzureError as e:
            print(f"Warning: Issue with search index: {str(e)}")
            pass

    def index_test_results(self, test_data):
        """Index test results"""
        try:
            documents = []
            env = test_data.get('environment', {})
            
            for test_file, tests in test_data.get('tests', {}).items():
                for test in tests:
                    doc = {
                        "id": str(uuid.uuid4()),
                        "testId": test.get('testId', ''),
                        "result": test.get('result', ''),
                        "duration": test.get('duration', ''),
                        "log": test.get('log', 'No log output captured.'),
                        "timestamp": self.processing_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "environment": {
                            "python": str(env.get('python', '')),
                            "platform": str(env.get('platform', '')),
                            "packages": {
                                "pytest": clean_version_string(str(env.get('packages', {}).get('pytest', ''))),
                                "pluggy": clean_version_string(str(env.get('packages', {}).get('pluggy', '')))
                            },
                            "plugins": {
                                "base_url": clean_version_string(str(env.get('plugins', {}).get('base_url', ''))),
                                "playwright": clean_version_string(str(env.get('plugins', {}).get('playwright', ''))),
                                "asyncio": clean_version_string(str(env.get('plugins', {}).get('asyncio', ''))),
                                "html": clean_version_string(str(env.get('plugins', {}).get('html', ''))),
                                "metadata": clean_version_string(str(env.get('plugins', {}).get('metadata', '')))
                            },
                            "platform_type": str(env.get('platform_type', '')),
                            "base_url": str(env.get('base_url', ''))
                        },
                        "metadata": {
                            "processor_version": self.config["METADATA"]["PROCESSOR_VERSION"],
                            "processed_by": self.config["METADATA"]["CURRENT_USER"],
                            "processed_at": self.processing_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
                        },
                        "title": test_data.get('title', '')
                    }
                    documents.append(doc)
                    print(f"Document to be indexed: {json.dumps(doc, indent=2)}")  # Debug print
            
            if documents:
                result = self.search_client.upload_documents(documents)
                print(f"Indexed {len(documents)} test results successfully")
                
        except AzureError as e:
            print(f"Error indexing test results: {str(e)}")
            raise

    def execute(self):
        """Main execution flow"""
        print(f"\nAzure Test Report Processor - v{self.config['METADATA']['PROCESSOR_VERSION']}")
        print(f"Started by: {self.config['METADATA']['CURRENT_USER']}")
        print(f"Time: {self.config['METADATA']['PROCESSING_TIME']}")
        print("=" * 50)
        
        success = True
        
        try:
            # Setup storage and upload file
            share = self.setup_file_share()
            
            # Extract and process test data
            test_data = self.extract_test_data(share)
            print("\nSuccessfully extracted test data:")
            print(json.dumps(test_data, indent=2))
            
            # Ensure search service exists and is configured
            self.ensure_search_service_exists()
            
            # Setup search index
            self.create_search_index()
            
            # Index test results
            self.index_test_results(test_data)
            
        except Exception as e:
            print(f"\nError: {str(e)}")
            print("Processing failed")
            success = False
            raise
        
        finally:
            if success:
                print("\nAll operations completed successfully")
            else:
                print("\nSome operations failed - check logs for details")

if __name__ == "__main__":
    processor = TestReportProcessor(CONFIG)
    processor.execute()
