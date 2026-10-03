"""Laravel graphs do not depend on the string hash seed (#79): a property read on a receiver that can be several
Eloquent models (`Income|Expense $document`) in a `??` fallback chain resolved to the column of whichever model a set
iteration produced first, so two indexes of one commit could differ. Every candidate model's column is read now (one
fallback step, edges marked ambiguous), narrowed to the tables whose migrations declare the column. Fixtures are
written from scratch in a temp dir."""
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sample import needs_php  # noqa: E402

FILES = {
    "composer.json": '{"require": {"laravel/framework": "^11.0"}, "autoload": {"psr-4": {"App\\\\": "app/"}}}',
    "artisan": "",
    "app/Models/Income.php": """
        <?php
        namespace App\\Models;
        use Illuminate\\Database\\Eloquent\\Model;
        class Income extends Model { protected $table = 'incomes'; }
        """,
    "app/Models/Expense.php": """
        <?php
        namespace App\\Models;
        use Illuminate\\Database\\Eloquent\\Model;
        class Expense extends Model { protected $table = 'expenses'; }
        """,
    "database/migrations/2024_01_01_000000_create_entries.php": """
        <?php
        use Illuminate\\Database\\Migrations\\Migration;
        use Illuminate\\Database\\Schema\\Blueprint;
        use Illuminate\\Support\\Facades\\Schema;
        return new class extends Migration {
            public function up(): void {
                Schema::create('incomes', function (Blueprint $table) {
                    $table->id(); $table->timestamp('paid_at')->nullable(); $table->timestamp('cleared_at')->nullable(); $table->timestamps();
                });
                Schema::create('expenses', function (Blueprint $table) {
                    $table->id(); $table->timestamp('paid_at')->nullable(); $table->timestamps();
                });
            }
        };
        """,
    "app/Services/Settlement.php": """
        <?php
        namespace App\\Services;
        use App\\Models\\Income;
        use App\\Models\\Expense;
        class Settlement {
            public function confirmedAt(Income|Expense $document) {
                $confirmedAt = $document->paid_at ?? $document->created_at ?? now();
                return $confirmedAt;
            }

            public function clearedAt(Income|Expense $document) {
                $clearedAt = $document->cleared_at ?? $document->paid_at;
                return $clearedAt;
            }
        }
        """,
}


def _index(root: Path, db: Path, seed: str):
    env = {**os.environ, "PYTHONHASHSEED": seed}
    subprocess.run([sys.executable, "-m", "codegraph.cli", "index", str(root), "--db", str(db), "--name", "r"],
                   cwd=ROOT, env=env, check=True, capture_output=True)
    c = sqlite3.connect(db)
    nodes = sorted(c.execute("SELECT id, kind, attrs FROM nodes"))
    edges = sorted(c.execute("SELECT src, dst, kind, confidence, attrs FROM edges"))
    return nodes, edges


@needs_php
def test_multi_model_fallback_chain_is_seed_independent(tmp_path):
    root = tmp_path / "app"
    for rel, body in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip("\n"))
    # seeds 1 and 3 picked different tables before
    g1, g3 = _index(root, tmp_path / "s1.db", "1"), _index(root, tmp_path / "s3.db", "3")
    assert g1 == g3
    fb = {}
    for src, dst, kind, _, attrs in g1[1]:
        if kind == "FALLS_BACK_TO":
            a = json.loads(attrs or "{}")
            fb.setdefault(src.split("#")[-1].split("@")[0], set()).add((a["order"], dst, tuple(a.get("candidates") or ())))
    both = ("expenses.paid_at", "incomes.paid_at")
    assert fb["$confirmedAt"] == {
        (1, "column:expenses.paid_at", both), (1, "column:incomes.paid_at", both),
        (2, "column:expenses.created_at", ("expenses.created_at", "incomes.created_at")),
        (2, "column:incomes.created_at", ("expenses.created_at", "incomes.created_at"))}
    # cleared_at exists on incomes only: that table, not an invented expenses.cleared_at
    assert fb["$clearedAt"] == {(1, "column:incomes.cleared_at", ()),
                                (2, "column:expenses.paid_at", both), (2, "column:incomes.paid_at", both)}
    assert not any(n[0] == "column:expenses.cleared_at" for n in g1[0])
