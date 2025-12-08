import os

from abc import ABC, abstractmethod
from dataclasses import dataclass
import duckdb

@dataclass
class ConnectionConfig:
    device: str = ""
    backend: str = ""
    use_fdp: bool = False
    memory: int = 0
    threads: int = 0

class Database(ABC):
    def __init__(self, db_path: str, threads:int, memory: int):
        self.db_path = db_path
        self.connection: duckdb.DuckDBPyConnection = None
        self.memory = memory
        self.threads = threads
        self._setup()
    
    @abstractmethod
    def _setup(self):
        pass

    @property
    def get_is_connected(self):
        return self.connection is not None

    def _connect(self):
        if not self.get_is_connected:
            self.connection: duckdb.DuckDBPyConnection = duckdb.connect(
                config={
                    "allow_unsigned_extensions": "true", 
                    "max_temp_directory_size": "150GB", 
                    "memory_limit": f"{self.memory}MB", 
                    "threads": self.threads,
                }
            )
    
    def query(self, query: str):
        return self.connection.query(query).fetchall()

    def execute(self, query: str):
        return self.connection.execute(query)

    def add_extension(self, name: str):
        self.connection.load_extension(name)
    
    def install_extension(self, name: str):
        self.connection.install_extension(name)
    
    def close(self):
        self.connection.close()
        self.connection = None

    def setup_tpch(self, input_dir_path: str, scale_factor: int):
        self._connect()

        input_file_path = os.path.join(input_dir_path, f"tpch-sf{scale_factor}.db")

        self.install_extension("tpch")
        self.add_extension("tpch")

        self.execute(f"CALL dbgen(sf={scale_factor});")

    def tpch(self, query: int):
        return self.query(f"PRAGMA tpch({query});")

    def disable_object_cache(self):
        self.execute("PRAGMA disable_object_cache;")

    def set_memory_limit(self, memory_mb: int):
        self.execute(f"PRAGMA memory_limit='{memory_mb}MB';")

    def enable_profiling(self):
        self.execute("PRAGMA enable_profiling='json';")
        self.execute("PRAGMA profiling_output='profile.json';")
        self.execute("PRAGMA profiling_mode='detailed';")

class QuackDatabase(Database):
    """
    QuackDatabase is just a normal Database wrapper of DuckDB
    """

    def __init__(self, db_path: str, threads: int, memory: int):
        super().__init__(db_path, threads, memory)
        super()._connect()

        self.execute(f"ATTACH DATABASE '{self.db_path}' AS bench (READ_WRITE);")
        self.execute("USE bench;")
    
    def _setup(self):
        print("Setting up QuackDatabase")
        return

class ConcurrentDatabase(Database):
    """
    ConcurrentDatabase is a Database wrapper of DuckDB that uses the concurrent connection
    """

    def __init__(self, db_path: str, threads:int, memory: int, connection: duckdb.DuckDBPyConnection):
        super().__init__(db_path, threads, memory)
        self.connection = connection

    
    def _setup(self):
        print("Setting up ConcurrentDatabase")
        return

class SPDKDatabase(Database):
    def __init__(self, db_path: str, threads:int, memory: int, config: ConnectionConfig):
        super().__init__(db_path, threads, memory)
        self.number_of_fdp_handles = 7
        self.device_path = config.device
        self.use_fdp = config.use_fdp
        self.backend = config.backend

    def _setup(self):
        print("Setting up SPDKDatabase")
        extension_path = os.path.abspath(f"/home/group01/nvmefs/build/release/extension/nvmefs/nvmefs.duckdb_extension")
        super()._connect()
        self.install_extension(extension_path)
        self.add_extension("nvmefs")
        self.execute(f"""CREATE OR REPLACE PERSISTENT SECRET nvmefs (
                        TYPE NVMEFS,
                        nvme_device_path '{self.device_path}',
                        backend          '{self.backend}'
                    );""")
        self.execute(f"ATTACH DATABASE '{self.db_path}' AS bench (READ_WRITE);")
        self.execute("USE bench;")
        self.disable_object_cache()
        

class NvmeDatabase(Database):
    """
    NvmeDatabase is a Database wrapper of DuckDB that uses NVMe as the storage backend
    """

    def __init__(self, db_path: str, threads: int, memory: int, config: ConnectionConfig):
        super().__init__(db_path, threads, memory)
        self.device_path = config.device
        self.backend = config.backend
        self.use_fdp = config.use_fdp
        self.number_of_fdp_handles = 7
    
    def _setup(self):
        extension_path = os.path.abspath(f"/home/group01/nvmefs/build/release/extension/nvmefs/nvmefs.duckdb_extension")
        super()._connect()
        self.install_extension(extension_path)
        self.add_extension("nvmefs")
        self.execute(f"""CREATE OR REPLACE PERSISTENT SECRET nvmefs (
                        TYPE NVMEFS,
                        nvme_device_path '{self.device_path}',
                        backend          '{self.backend}'
                    );""")
        
        self.execute(f"ATTACH DATABASE '{self.db_path}' AS bench (READ_WRITE);")
        self.execute("USE bench;")
        self.disable_object_cache()

