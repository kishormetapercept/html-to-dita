from dataclasses import dataclass
from threading import Lock
from typing import Dict


@dataclass
class UserState:
    input_dir: str = ""
    output_dir: str = ""
    input_file_name: str = "output.zip"


class RuntimeStateStore:
    def __init__(self) -> None:
        self._states: Dict[str, UserState] = {}
        self._lock = Lock()

    def _get_or_create(self, user_id: str) -> UserState:
        if user_id not in self._states:
            self._states[user_id] = UserState()
        return self._states[user_id]

    def get(self, user_id: str) -> UserState:
        with self._lock:
            return self._get_or_create(user_id)

    def set_paths(self, user_id: str, input_dir: str, output_dir: str) -> None:
        with self._lock:
            state = self._get_or_create(user_id)
            state.input_dir = input_dir
            state.output_dir = output_dir

    def set_input_file_name(self, user_id: str, file_name: str) -> None:
        with self._lock:
            state = self._get_or_create(user_id)
            state.input_file_name = file_name

    def clear_paths(self, user_id: str) -> None:
        with self._lock:
            state = self._get_or_create(user_id)
            state.input_dir = ""
            state.output_dir = ""

    def clear_input_file_name(self, user_id: str) -> None:
        with self._lock:
            state = self._get_or_create(user_id)
            state.input_file_name = "output.zip"


state_store = RuntimeStateStore()
