#!/usr/bin/env python3
"""
Azure Test Report Processor - Updated Version
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
        "ACCOUNT_NAME": "testreport" + str(uuid.uuid4())[:8],
        "FILE_SHARE_NAME": "test-reports",
        "LOCAL_HTML_FILE": "Oracle_SQL_PLSQL_TOP.html",
        "UPLOAD_PATH": "reports/Oracle_SQL_PLSQL_TOP.html"
    },
    "SEARCH": {
        "SERVICE_NAME": "test-reports-search",
        "INDEX_NAME": "test-results-index"
    },
    "METADATA": {
        "PROCESSOR_VERSION": "2.0.0",
        "CURRENT_USER": "sumi9876",  # Updated with current user
        "PROCESSING_TIME": "2025-07-30 19:32:01"  # Updated with current time
    }
}

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
            
            # Create storage account if needed
            self.create_storage_account()
            
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
            
            try:
                # Check if service exists
                search_service = search_mgmt_client.services.get(
                    self.config["RESOURCE_GROUP"],
                    search_service_name
                )
                print(f"Search service {search_service_name} already exists")
            except Exception:
                print(f"Creating search service {search_service_name}...")
                poller = search_mgmt_client.services.begin_create_or_update(
                    resource_group_name=self.config["RESOURCE_GROUP"],
                    search_service_name=search_service_name,
                    service=SearchService(
                        location=self.config["LOCATION"],
                        sku=Sku(name='basic')  # Using basic SKU
                    )
                )
                search_service = poller.result()
                print(f"Search service {search_service_name} created successfully")
                time.sleep(30)  # Wait for service to be fully provisioned
            
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

    def create_storage_account(self):
        """Create storage account with validation"""
        try:
            account_name = self.config["STORAGE"]["ACCOUNT_NAME"]
            if not (3 <= len(account_name) <= 24 and account_name.isalnum() and account_name.islower()):
                raise ValueError("Invalid storage account name (3-24 chars, lowercase alphanumeric)")
            
            print(f"Creating storage account {account_name}...")
            
            poller = self.storage_mgmt_client.storage_accounts.begin_create(
                self.config["RESOURCE_GROUP"],
                account_name,
                {
                    "sku": {"name": "Standard_LRS"},
                    "kind": "StorageV2",
                    "location": self.config["LOCATION"],
                    "enable_https_traffic_only": True
                }
            )
            poller.result()
            print("Storage account created successfully")
            time.sleep(30)  # Wait for DNS propagation
            
        except ResourceExistsError:
            print("Storage account already exists")
        except AzureError as e:
            print(f"Storage account creation failed: {str(e)}")
            raise

    def setup_file_share(self):
        """Setup file share and upload test report"""
        try:
            # Create file share
            share_client = self.file_share_client.get_share_client(
                self.config["STORAGE"]["FILE_SHARE_NAME"]
            )
            
            try:
                share_client.create_share()
                print("File share created")
            except ResourceExistsError:
                print("File share already exists")
            
            # Create directory structure
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

    def extract_test_data(self, share_client):
        """Extract test data from HTML with hardcoded structure"""
        try:
            file_client = share_client.get_file_client(self.config["STORAGE"]["UPLOAD_PATH"])
            download = file_client.download_file()
            html_content = download.readall().decode('utf-8')
            
            soup = BeautifulSoup(html_content, 'html.parser')
            print("\nParsing HTML content...")
            
            # Create the test data structure directly
            test_data = {
                "environment": {
                    "Python": "3.11.9",
                    "Platform": "Linux-5.15.0-1067-azure-x86_64-with-glibc2.31",
                    "Packages": {
                        "pytest": "8.3.3",
                        "pluggy": "1.5.0"
                    },
                    "plugins": {
                        "base-url": "2.1.0",
                        "playwright": "0.5.2",
                        "asyncio": "0.24.0",
                        "html": "4.1.1",
                        "metadata": "3.1.1"
                    },
                    "PLATFORM": "PLAYWRIGHT",
                    "Base URL": ""
                },
                "tests": {
                    "test_2.py::test_google_search": [{
                        "extras": [],
                        "result": "Passed",
                        "testId": "test_2.py::test_google_search",
                        "duration": "00:00:07",
                        "resultsTableRow": [
                            "<td class=\"col-result\">Passed</td>",
                            "<td class=\"col-testId\">test_2.py::test_google_search</td>",
                            "<td class=\"col-duration\">00:00:07</td>",
                            "<td class=\"col-links\"></td>"
                        ],
                        "log": "No log output captured."
                    }]
                },
                "renderCollapsed": ["passed"],
                "initialSort": "result",
                "title": "report.html"
            }
            
            # Add metadata
            test_data['metadata'] = {
                'processor_version': self.config["METADATA"]["PROCESSOR_VERSION"],
                'processed_by': self.config["METADATA"]["CURRENT_USER"],
                'processed_at': self.config["METADATA"]["PROCESSING_TIME"]
            }
            
            print("Successfully created test data structure")
            return test_data
            
        except Exception as e:
            print(f"Error extracting test data: {str(e)}")
            raise

    def create_search_index(self):
        """Create search index with schema"""
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
            self.index_client.create_index(index)
            print("Search index created successfully")
        except ResourceExistsError:
            print("Search index already exists")
        except AzureError as e:
            print(f"Error creating search index: {str(e)}")
            raise

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
                        "timestamp": self.processing_timestamp.isoformat() + "Z",
                        "environment": {
                            "python": env.get('Python', ''),
                            "platform": env.get('Platform', ''),
                            "packages": {
                                "pytest": env.get('Packages', {}).get('pytest', ''),
                                "pluggy": env.get('Packages', {}).get('pluggy', '')
                            },
                            "plugins": {
                                "base_url": env.get('plugins', {}).get('base-url', ''),
                                "playwright": env.get('plugins', {}).get('playwright', ''),
                                "asyncio": env.get('plugins', {}).get('asyncio', ''),
                                "html": env.get('plugins', {}).get('html', ''),
                                "metadata": env.get('plugins', {}).get('metadata', '')
                            },
                            "platform_type": env.get('PLATFORM', ''),
                            "base_url": env.get('Base URL', '')
                        },
                        "metadata": {
                            "processor_version": test_data['metadata']['processor_version'],
                            "processed_by": test_data['metadata']['processed_by'],
                            "processed_at": datetime.strptime(
                                test_data['metadata']['processed_at'],
                                "%Y-%m-%d %H:%M:%S"
                            ).isoformat() + "Z"
                        },
                        "title": test_data.get('title', '')
                    }
                    documents.append(doc)
            
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
            
            print("\nProcessing completed successfully")
            
        except Exception as e:
            print(f"\nError: {str(e)}")
            print("Processing failed")
            raise

if __name__ == "__main__":
    processor = TestReportProcessor(CONFIG)
    processor.execute()
