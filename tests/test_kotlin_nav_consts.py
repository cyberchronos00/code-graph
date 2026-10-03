"""#67: Compose Navigation routes held in string constants (`composable(Destinations.TASKS_ROUTE)`, the
android/architecture-samples layout), and navigate() strings built from them."""
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_kotlin")
from codegraph.indexer import index_project  # noqa: E402

FIX = Path(__file__).resolve().parent / "kotlin_nav_consts"


def test_routes_from_constants(tmp_path):
    index_project(FIX, tmp_path / "g.db", "nav")
    c = sqlite3.connect(tmp_path / "g.db")
    pages = {r[0] for r in c.execute("SELECT id FROM nodes WHERE kind='page'")}
    assert pages == {"page:kotlin:tasks?userMessage={userMessage}", "page:kotlin:task/{taskId}"}
    nav = set(c.execute("SELECT src, dst, confidence FROM edges WHERE kind='NAVIGATES_TO'"))
    assert nav == {("method:com.ex.TodoNavigationActions.navigateToTasks", "page:kotlin:tasks?userMessage={userMessage}", "exact"),
                   ("method:com.ex.TodoNavigationActions.navigateToTaskDetail", "page:kotlin:task/{taskId}", "exact")}
    calls = set(c.execute("SELECT src, dst FROM edges WHERE src LIKE 'page:%' AND kind='CALLS'"))
    assert ("page:kotlin:task/{taskId}", "function:com.ex.TaskDetailScreen") in calls
    assert ("page:kotlin:tasks?userMessage={userMessage}", "function:com.ex.TasksScreen") in calls
