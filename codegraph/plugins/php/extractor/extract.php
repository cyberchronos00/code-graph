<?php
/**
 * code-graph PHP fact extractor (deterministic, nikic/php-parser v5).
 *
 * Usage: php extract.php <project_root> <file-list.txt> > facts.jsonl
 * Emits one JSON object per file: declarations (classes/methods/props/functions)
 * and in-order "facts" (calls, news, property fetches, assignments, class refs)
 * with symbolic receiver descriptors. Type resolution across files happens in
 * the Python core; this script only resolves names (use statements -> FQCN).
 */
declare(strict_types=1);
require __DIR__ . '/vendor/autoload.php';

use PhpParser\Node;
use PhpParser\Node\Expr;
use PhpParser\Node\Stmt;
use PhpParser\Node\Scalar;
use PhpParser\NodeTraverser;
use PhpParser\NodeVisitor\NameResolver;
use PhpParser\ParserFactory;

ini_set('memory_limit', '2G');
$root = rtrim($argv[1], '/');
$files = array_filter(array_map('trim', file($argv[2])));
$parser = (new ParserFactory())->createForNewestSupportedVersion();

final class Ctx {
    public ?string $class = null;      // current class FQCN
    public ?string $parent = null;     // parent FQCN
    public array $facts = [];          // collected facts for current function-like
    public array $closureStack = [];   // enclosing call contexts for closures
    public array $ranges = [];         // spl_object_id(node) => [lo, hi) fact-index range of the subtree
    public array $nodeFact = [];       // spl_object_id(call node) => fact index
}

function nameStr($n): ?string {
    if ($n instanceof Node\Name) { return $n->toString(); }
    if ($n instanceof Node\Identifier) { return $n->toString(); }
    return null;
}

function resolveSpecial(?string $name, Ctx $c): ?string {
    if ($name === null) return null;
    $l = strtolower($name);
    if ($l === 'self' || $l === 'static') return $c->class;
    if ($l === 'parent') return $c->parent;
    return ltrim($name, '\\');
}

function typeList($t, ?\PhpParser\NameContext $nc = null): array {
    if ($t === null) return [];
    if ($t instanceof Node\NullableType) return typeList($t->type, $nc);
    if ($t instanceof Node\UnionType || $t instanceof Node\IntersectionType) {
        $o = []; foreach ($t->types as $x) { $o = array_merge($o, typeList($x, $nc)); } return $o;
    }
    if ($t instanceof Node\Name) return [ltrim($t->toString(), '\\')];
    if ($t instanceof Node\Identifier) return [$t->toString()];
    return [];
}

/** Resolve a docblock type string like "?Collection<int, Product>|Foo[]" into class names. */
function docTypes(string $s, \PhpParser\NameContext $nc): array {
    $out = [];
    $s = preg_replace('/<.*>/', '', $s);
    foreach (preg_split('/[|&]/', $s) as $part) {
        $part = trim($part, " ?()");
        $part = preg_replace('/\[\]$/', '', $part);
        if ($part === '' || !preg_match('/^\\\\?[A-Za-z_][A-Za-z0-9_\\\\]*$/', $part)) continue;
        $lower = strtolower($part);
        if (in_array($lower, ['int','string','bool','float','array','mixed','void','null','callable','iterable','object','false','true','self','static','$this','never','resource','list','non-empty-string','positive-int','class-string'], true)) {
            if (in_array($lower, ['self','static','$this'], true)) $out[] = $lower;
            continue;
        }
        $out[] = ltrim($nc->getResolvedClassName(new Node\Name($part))->toString(), '\\');
    }
    return $out;
}

function docText($node): ?string {
    $d = $node->getDocComment();
    return $d ? $d->getText() : null;
}

/** Symbolic descriptor of an expression (receiver / argument). */
function desc($e, Ctx $c, int $depth = 0) {
    if ($e === null) return null;
    if ($depth > 12) return ['k' => 'deep'];
    if ($e instanceof Node\Arg) { $e = $e->value; }
    if ($e instanceof Expr\Variable) {
        if (is_string($e->name)) return $e->name === 'this' ? ['k' => 'this'] : ['k' => 'var', 'n' => $e->name];
        return ['k' => 'dynvar'];
    }
    if ($e instanceof Scalar\String_) return ['k' => 'str', 'v' => $e->value];
    if ($e instanceof Scalar\Int_) return ['k' => 'int', 'v' => $e->value];
    if ($e instanceof Scalar\InterpolatedString) {
        $parts = [];
        foreach ($e->parts as $p) {
            if ($p instanceof Node\InterpolatedStringPart) $parts[] = ['k' => 'str', 'v' => $p->value];
            else $parts[] = desc($p, $c, $depth + 1);
        }
        return ['k' => 'interp', 'parts' => $parts];
    }
    if ($e instanceof Expr\BinaryOp\Concat) {
        return ['k' => 'concat', 'l' => desc($e->left, $c, $depth + 1), 'r' => desc($e->right, $c, $depth + 1)];
    }
    if ($e instanceof Expr\PropertyFetch || $e instanceof Expr\NullsafePropertyFetch) {
        return ['k' => 'prop', 'of' => desc($e->var, $c, $depth + 1), 'n' => nameStr($e->name)];
    }
    if ($e instanceof Expr\StaticPropertyFetch) {
        return ['k' => 'sprop', 'class' => resolveSpecial(nameStr($e->class), $c), 'n' => nameStr($e->name)];
    }
    if ($e instanceof Expr\MethodCall || $e instanceof Expr\NullsafeMethodCall) {
        return ['k' => 'mcall', 'of' => desc($e->var, $c, $depth + 1), 'm' => nameStr($e->name), 'args' => argsDesc($e->args, $c, $depth + 1)];
    }
    if ($e instanceof Expr\StaticCall) {
        return ['k' => 'scall', 'class' => resolveSpecial(nameStr($e->class), $c), 'm' => nameStr($e->name), 'args' => argsDesc($e->args, $c, $depth + 1)];
    }
    if ($e instanceof Expr\New_) {
        if ($e->class instanceof Stmt\Class_) return ['k' => 'new', 'class' => null];
        return ['k' => 'new', 'class' => resolveSpecial(nameStr($e->class), $c)];
    }
    if ($e instanceof Expr\FuncCall) {
        return ['k' => 'func', 'n' => nameStr($e->name), 'args' => argsDesc($e->args, $c, $depth + 1)];
    }
    if ($e instanceof Expr\ClassConstFetch) {
        $cn = nameStr($e->name);
        $cls = resolveSpecial(nameStr($e->class), $c);
        if ($cn === 'class') return ['k' => 'classconst', 'class' => $cls];
        return ['k' => 'const', 'class' => $cls, 'n' => $cn];
    }
    if ($e instanceof Expr\Array_) {
        $items = [];
        foreach ($e->items as $it) {
            if ($it === null) continue;
            $items[] = ['key' => $it->key ? desc($it->key, $c, $depth + 1) : null, 'v' => desc($it->value, $c, $depth + 1)];
            if (count($items) > 60) break;
        }
        return ['k' => 'arr', 'items' => $items];
    }
    if ($e instanceof Expr\Ternary) {
        return ['k' => 'alt', 'op' => $e->if === null ? 'elvis' : 'ternary', 'opts' => [desc($e->if ?? $e->cond, $c, $depth + 1), desc($e->else, $c, $depth + 1)]];  // see skel for conditions
    }
    if ($e instanceof Expr\BinaryOp\Coalesce) {
        return ['k' => 'alt', 'op' => 'coalesce', 'opts' => [desc($e->left, $c, $depth + 1), desc($e->right, $c, $depth + 1)]];
    }
    if ($e instanceof Expr\Cast) return desc($e->expr, $c, $depth + 1);
    if ($e instanceof Expr\Clone_) return desc($e->expr, $c, $depth + 1);
    if ($e instanceof Expr\ArrayDimFetch) {
        return ['k' => 'dim', 'of' => desc($e->var, $c, $depth + 1), 'key' => $e->dim === null ? null : desc($e->dim, $c, $depth + 1)];
    }
    if ($e instanceof Expr\Closure || $e instanceof Expr\ArrowFunction) return ['k' => 'closure'];
    if ($e instanceof Expr\ConstFetch) return ['k' => 'cfetch', 'n' => nameStr($e->name)];
    if ($e instanceof Expr\Match_) {
        $o = []; foreach ($e->arms as $a) { $o[] = desc($a->body, $c, $depth + 1); } return ['k' => 'alt', 'op' => 'match', 'opts' => $o];
    }
    if ($e instanceof Expr\Assign) return desc($e->expr, $c, $depth + 1);
    return ['k' => 'other', 't' => $e->getType()];
}

function argsDesc(array $args, Ctx $c, int $depth = 0): array {
    $o = [];
    foreach ($args as $i => $a) {
        if (!($a instanceof Node\Arg)) { $o[] = ['k' => 'variadic']; continue; }
        $d = desc($a->value, $c, $depth);
        if ($a->name) $d['named'] = $a->name->toString();
        $o[] = $d;
        if ($i > 12) break;
    }
    return $o;
}

function addFact(Ctx $c, array $f, Node $n): void {
    $f['line'] = $n->getStartLine();
    if (($f['t'] ?? null) === 'call') {
        $c->nodeFact[spl_object_id($n)] = count($c->facts);
        if ($n->getEndLine() > $f['line']) $f['end'] = $n->getEndLine();   // multi-line calls: closure bodies lie inside
    }
    if ($c->closureStack) $f['ctx'] = end($c->closureStack);
    $c->facts[] = $f;
}

/** Walk any node collecting facts; records the fact-index range of every subtree (for gating). */
function walk($node, Ctx $c, \PhpParser\NameContext $nc): void {
    if (is_array($node)) { foreach ($node as $n) walk($n, $c, $nc); return; }
    if (!($node instanceof Node)) return;
    $lo = count($c->facts);
    walkNode($node, $c, $nc);
    $c->ranges[spl_object_id($node)] = [$lo, count($c->facts)];
}

function walkNode(Node $node, Ctx $c, \PhpParser\NameContext $nc): void {
    // nested class-likes are handled at declaration level
    if ($node instanceof Stmt\ClassLike || $node instanceof Stmt\Function_) return;

    // inline @var docblocks
    $doc = $node->getDocComment();
    if ($doc && $node instanceof Stmt && preg_match_all('/@var\s+(\S+)\s+\$(\w+)/', $doc->getText(), $mm, PREG_SET_ORDER)) {
        foreach ($mm as $m) addFact($c, ['t' => 'vartype', 'var' => $m[2], 'types' => docTypes($m[1], $nc), 'src' => 'doc'], $node);
    }

    if ($node instanceof Expr\Assign || $node instanceof Expr\AssignRef || $node instanceof Expr\AssignOp\Coalesce) {
        $target = $node->var;
        if ($target instanceof Expr\Variable && is_string($target->name)) {
            addFact($c, ['t' => 'assign', 'var' => $target->name, 'expr' => desc($node->expr, $c)], $node);
        } elseif ($target instanceof Expr\PropertyFetch || $target instanceof Expr\NullsafePropertyFetch) {
            addFact($c, ['t' => 'fetch', 'recv' => desc($target->var, $c), 'prop' => nameStr($target->name), 'write' => true], $node);
            walk($target->var, $c, $nc);
            walk($node->expr, $c, $nc);
            return;
        } elseif ($target instanceof Expr\List_ || $target instanceof Expr\Array_) {
            // destructuring: ignore types
        }
    }
    if ($node instanceof Expr\Instanceof_ && $node->expr instanceof Expr\Variable && is_string($node->expr->name) && $node->class instanceof Node\Name) {
        addFact($c, ['t' => 'vartype', 'var' => $node->expr->name, 'types' => [ltrim($node->class->toString(), '\\')], 'src' => 'instanceof'], $node);
    }
    if ($node instanceof Stmt\Foreach_ && $node->valueVar instanceof Expr\Variable && is_string($node->valueVar->name)) {
        addFact($c, ['t' => 'assign', 'var' => $node->valueVar->name, 'expr' => ['k' => 'elem', 'of' => desc($node->expr, $c)]], $node);
    }
    if ($node instanceof Stmt\Catch_ && $node->var && is_string($node->var->name)) {
        addFact($c, ['t' => 'vartype', 'var' => $node->var->name, 'types' => array_map(fn($t) => ltrim($t->toString(), '\\'), $node->types), 'src' => 'catch'], $node);
    }
    if ($node instanceof Expr\MethodCall || $node instanceof Expr\NullsafeMethodCall) {
        addFact($c, ['t' => 'call', 'kind' => 'method', 'recv' => desc($node->var, $c), 'm' => nameStr($node->name), 'args' => argsDesc($node->args, $c)], $node);
    } elseif ($node instanceof Expr\StaticCall) {
        addFact($c, ['t' => 'call', 'kind' => 'static', 'class' => resolveSpecial(nameStr($node->class), $c), 'm' => nameStr($node->name), 'args' => argsDesc($node->args, $c), 'parentcall' => (nameStr($node->class) === 'parent')], $node);
    } elseif ($node instanceof Expr\FuncCall) {
        addFact($c, ['t' => 'call', 'kind' => 'func', 'n' => nameStr($node->name), 'args' => argsDesc($node->args, $c)], $node);
    } elseif ($node instanceof Expr\New_) {
        $cls = ($node->class instanceof Stmt\Class_) ? null : resolveSpecial(nameStr($node->class), $c);
        addFact($c, ['t' => 'new', 'class' => $cls, 'args' => argsDesc($node->args, $c)], $node);
    } elseif ($node instanceof Expr\PropertyFetch || $node instanceof Expr\NullsafePropertyFetch) {
        addFact($c, ['t' => 'fetch', 'recv' => desc($node->var, $c), 'prop' => nameStr($node->name), 'write' => false], $node);
    } elseif ($node instanceof Expr\StaticPropertyFetch) {
        addFact($c, ['t' => 'sfetch', 'class' => resolveSpecial(nameStr($node->class), $c), 'prop' => nameStr($node->name)], $node);
    } elseif ($node instanceof Expr\ClassConstFetch) {
        $cls = resolveSpecial(nameStr($node->class), $c);
        if ($cls) addFact($c, ['t' => 'classref', 'class' => $cls, 'const' => nameStr($node->name)], $node);
    } elseif ($node instanceof Stmt\Return_ && $node->expr) {
        addFact($c, ['t' => 'return', 'expr' => desc($node->expr, $c)], $node);
    }

    // closures: params typed become var types; closures get context of enclosing call
    if ($node instanceof Expr\Closure || $node instanceof Expr\ArrowFunction) {
        foreach ($node->params as $p) {
            if ($p->var instanceof Expr\Variable && is_string($p->var->name)) {
                $ts = typeList($p->type);
                if ($ts) addFact($c, ['t' => 'vartype', 'var' => $p->var->name, 'types' => $ts, 'src' => 'closure_param'], $node);
            }
        }
    }

    // push call context for closures passed as arguments (used by migrations, Route groups, Schedule)
    $pushed = false;
    if (($node instanceof Expr\StaticCall || $node instanceof Expr\MethodCall || $node instanceof Expr\FuncCall)) {
        $hasClosure = false; $clo = null;
        foreach ($node->args as $a) { if ($a instanceof Node\Arg && ($a->value instanceof Expr\Closure || $a->value instanceof Expr\ArrowFunction)) { $hasClosure = true; $clo = $a->value; break; } }
        if ($hasClosure) {
            // end: last line of the call (closure bodies of Broadcast::channel / Pest test() / Route groups lie inside)
            // cparams: the closure's parameter names (the first one of a channel callback is the authenticated user)
            $cc = ['m' => nameStr($node->name), 'line' => $node->getStartLine(), 'end' => $node->getEndLine(),
                   'cparams' => array_values(array_map(fn($p) => ($p->var instanceof Expr\Variable && is_string($p->var->name)) ? $p->var->name : '?', $clo->params))];
            if ($node instanceof Expr\StaticCall) $cc['class'] = resolveSpecial(nameStr($node->class), $c);
            $first = $node->args[0] ?? null;
            if ($first instanceof Node\Arg && $first->value instanceof Scalar\String_) $cc['arg0'] = $first->value->value;
            $c->closureStack[] = $cc; $pushed = true;
        }
    }
    foreach ($node->getSubNodeNames() as $sn) {
        walk($node->$sn, $c, $nc);
    }
    if ($pushed) array_pop($c->closureStack);
}

function literal($e) {
    if ($e instanceof Scalar\String_) return $e->value;
    if ($e instanceof Scalar\Int_ || $e instanceof Scalar\Float_) return $e->value;
    if ($e instanceof Expr\ConstFetch) { $n = strtolower($e->name->toString()); return $n === 'true' ? true : ($n === 'false' ? false : null); }
    if ($e instanceof Expr\Array_) {
        $o = [];
        foreach ($e->items as $it) {
            if ($it === null) continue;
            $v = literal($it->value);
            if ($it->key instanceof Scalar\String_) $o[$it->key->value] = $v; else $o[] = $v;
        }
        return $o;
    }
    if ($e instanceof Expr\ClassConstFetch && nameStr($e->name) === 'class') return ['__class__' => ltrim(nameStr($e->class), '\\')];
    return null;
}

function funcLike($fn, Ctx $c, \PhpParser\NameContext $nc, ?string $doc): array {
    $c->facts = []; $c->closureStack = [];
    $params = [];
    $docParams = [];
    if ($doc && preg_match_all('/@param\s+(\S+)\s+\$(\w+)/', $doc, $mm, PREG_SET_ORDER)) {
        foreach ($mm as $m) $docParams[$m[2]] = docTypes($m[1], $nc);
    }
    foreach ($fn->params as $p) {
        $pn = ($p->var instanceof Expr\Variable && is_string($p->var->name)) ? $p->var->name : '?';
        $ts = typeList($p->type);
        if (!$ts && isset($docParams[$pn])) $ts = $docParams[$pn];
        $params[] = ['name' => $pn, 'types' => $ts, 'promoted' => $p->flags !== 0, 'line' => $p->getStartLine()];
    }
    $returns = typeList($fn->returnType ?? null);
    $nullable = ($fn->returnType ?? null) instanceof Node\NullableType
        || (($fn->returnType ?? null) instanceof Node\UnionType && in_array('null', array_map(fn($t) => strtolower((string) $t), $fn->returnType->types), true));
    if ($doc && preg_match('/@return\s+(\S+)/', $doc, $m)) {
        $returns = array_values(array_unique(array_merge($returns, docTypes($m[1], $nc))));
    }
    if (isset($fn->stmts) && $fn->stmts !== null) walk($fn->stmts, $c, $nc);
    if ($fn instanceof Expr\ArrowFunction) walk($fn->expr, $c, $nc);
    $skel = (isset($fn->stmts) && $fn->stmts !== null) ? skelStmts($fn->stmts, $c) : [];
    return ['params' => $params, 'returns' => $returns, 'returns_nullable' => $nullable, 'facts' => $c->facts, 'skel' => $skel];
}

function classLike(Stmt\ClassLike $cl, Ctx $c, \PhpParser\NameContext $nc, string $rel): array {
    $kind = $cl instanceof Stmt\Interface_ ? 'interface' : ($cl instanceof Stmt\Trait_ ? 'trait' : ($cl instanceof Stmt\Enum_ ? 'enum' : 'class'));
    $fq = isset($cl->namespacedName) && $cl->namespacedName ? $cl->namespacedName->toString() : ('class@anonymous:' . $rel . ':' . $cl->getStartLine());
    $c->class = $fq;
    $extends = [];
    if ($cl instanceof Stmt\Class_ && $cl->extends) $extends = [ltrim($cl->extends->toString(), '\\')];
    if ($cl instanceof Stmt\Interface_) $extends = array_map(fn($n) => ltrim($n->toString(), '\\'), $cl->extends);
    $c->parent = $extends[0] ?? null;
    $impl = ($cl instanceof Stmt\Class_ || $cl instanceof Stmt\Enum_) ? array_map(fn($n) => ltrim($n->toString(), '\\'), $cl->implements) : [];
    $traits = []; $props = []; $methods = []; $consts = []; $cases = [];
    $cdoc = docText($cl);
    $docProps = [];
    if ($cdoc && preg_match_all('/@property(?:-read|-write)?\s+(\S+)\s+\$(\w+)/', $cdoc, $mm, PREG_SET_ORDER)) {
        foreach ($mm as $m) $docProps[] = ['name' => $m[2], 'types' => docTypes($m[1], $nc), 'src' => 'doc'];
    }
    foreach ($cl->stmts as $s) {
        if ($s instanceof Stmt\TraitUse) {
            foreach ($s->traits as $t) $traits[] = ltrim($t->toString(), '\\');
        } elseif ($s instanceof Stmt\Property) {
            $pd = docText($s);
            $ts = typeList($s->type);
            if (!$ts && $pd && preg_match('/@var\s+(\S+)/', $pd, $m)) $ts = docTypes($m[1], $nc);
            foreach ($s->props as $p) {
                $props[] = ['name' => $p->name->toString(), 'types' => $ts, 'static' => $s->isStatic(), 'default' => $p->default ? literal($p->default) : null,
                    'line' => $s->getStartLine(), 'doc' => $pd];
            }
        } elseif ($s instanceof Stmt\EnumCase) {
            $cases[] = ['name' => $s->name->toString(), 'line' => $s->getStartLine()];
        } elseif ($s instanceof Stmt\ClassConst) {
            foreach ($s->consts as $k) $consts[] = ['name' => $k->name->toString(), 'value' => literal($k->value), 'line' => $s->getStartLine()];
        } elseif ($s instanceof Stmt\ClassMethod) {
            $md = docText($s);
            $f = funcLike($s, $c, $nc, $md);
            if ($s->name->toString() === '__construct') {
                foreach ($f['params'] as $p) if ($p['promoted']) $props[] = ['name' => $p['name'], 'types' => $p['types'], 'static' => false, 'default' => null, 'line' => $p['line'], 'doc' => null, 'promoted' => true];
            }
            $attrNames = [];
            foreach ($s->attrGroups as $ag) foreach ($ag->attrs as $at) $attrNames[] = ltrim($at->name->toString(), '\\');
            $methods[] = ['name' => $s->name->toString(), 'static' => $s->isStatic(), 'abstract' => $s->isAbstract() || $cl instanceof Stmt\Interface_,
                'attributes' => $attrNames,
                'visibility' => $s->isPublic() ? 'public' : ($s->isProtected() ? 'protected' : 'private'),
                'line' => $s->getStartLine(), 'end_line' => $s->getEndLine(), 'doc' => $md] + $f;
        }
    }
    foreach ($docProps as $dp) $props[] = $dp + ['static' => false, 'default' => null, 'line' => $cl->getStartLine(), 'doc' => null];
    $abstract = $cl instanceof Stmt\Class_ ? $cl->isAbstract() : false;
    return ['kind' => $kind, 'fqcn' => $fq, 'extends' => $extends, 'implements' => $impl, 'traits' => $traits, 'abstract' => $abstract,
        'line' => $cl->getStartLine(), 'end_line' => $cl->getEndLine(), 'doc' => $cdoc, 'props' => $props, 'consts' => $consts, 'cases' => $cases, 'methods' => $methods];
}

/** Collect class-likes anywhere in the tree (incl. anonymous classes in `return new class ...`). */
class Collector extends \PhpParser\NodeVisitorAbstract {
    public array $classes = []; public array $functions = [];
    public function __construct(public string $rel, public \PhpParser\NameContext $nc) {}
    public function enterNode(Node $n) {
        if ($n instanceof Stmt\ClassLike) { $this->classes[] = $n; }
        if ($n instanceof Stmt\Function_) { $this->functions[] = $n; }
        if ($n instanceof Expr\New_ && $n->class instanceof Stmt\Class_) { $this->classes[] = $n->class; }
        return null;
    }
}

$out = fopen('php://stdout', 'w');
foreach ($files as $rel) {
    $path = $root . '/' . $rel;
    $code = @file_get_contents($path);
    if ($code === false) continue;
    $GLOBALS['branchyMemo'] = [];
    $rec = ['file' => $rel, 'classes' => [], 'functions' => [], 'top' => null, 'error' => null, 'namespace' => null, 'uses' => []];
    try {
        $ast = $parser->parse($code);
        $nr = new NameResolver(null, ['preserveOriginalNames' => true, 'replaceNodes' => true]);
        $tr = new NodeTraverser(); $tr->addVisitor($nr);
        $ast = $tr->traverse($ast);
        $nc = $nr->getNameContext();
        foreach ($ast as $st) {
            if ($st instanceof Stmt\Namespace_) { $rec['namespace'] = $st->name ? $st->name->toString() : null; }
        }
        $col = new Collector($rel, $nc);
        $t2 = new NodeTraverser(); $t2->addVisitor($col); $t2->traverse($ast);
        foreach ($col->classes as $cl) {
            $c = new Ctx();
            $rec['classes'][] = classLike($cl, $c, $nc, $rel);
        }
        foreach ($col->functions as $fn) {
            $c = new Ctx();
            $f = funcLike($fn, $c, $nc, docText($fn));
            $rec['functions'][] = ['name' => $fn->namespacedName ? $fn->namespacedName->toString() : $fn->name->toString(), 'line' => $fn->getStartLine(), 'end_line' => $fn->getEndLine(), 'doc' => docText($fn)] + $f;
        }
        // top-level (file scope) code: routes, config, console schedules, migrations' `return new class`
        $c = new Ctx();
        $top = [];
        foreach ($ast as $st) {
            $stmts = $st instanceof Stmt\Namespace_ ? $st->stmts : [$st];
            foreach ($stmts as $s) {
                if ($s instanceof Stmt\ClassLike || $s instanceof Stmt\Function_ || $s instanceof Stmt\Use_ || $s instanceof Stmt\GroupUse) continue;
                $top[] = $s;
            }
        }
        $c->facts = [];
        walk($top, $c, $nc);
        $rec['top'] = $c->facts;
        // config files: literal structure of `return [...]` with env() calls
        if (str_starts_with($rel, 'config/')) {
            foreach ($top as $s) if ($s instanceof Stmt\Return_ && $s->expr instanceof Expr\Array_) $rec['config'] = configTree($s->expr, $c);
        }
        if (preg_match('#^routes/#', $rel)) {
            $rec['routes'] = routeWalk($top, ['prefix' => '', 'middleware' => [], 'name' => '', 'controller' => null], $c);
        }
    } catch (\Throwable $e) {
        $rec['error'] = get_class($e) . ': ' . $e->getMessage();
    }
    fwrite($out, json_encode($rec, JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR) . "\n");
}

/** config/*.php: walk returned array; leaves carry env() reads and literal defaults. */
function configTree(Expr\Array_ $a, Ctx $c, string $prefix = ''): array {
    $out = [];
    foreach ($a->items as $it) {
        if ($it === null || !($it->key instanceof Scalar\String_)) continue;
        $key = ($prefix === '' ? '' : $prefix . '.') . $it->key->value;
        $node = ['key' => $key, 'line' => $it->getStartLine(), 'envs' => [], 'literal' => null, 'children' => []];
        $v = $it->value;
        if ($v instanceof Expr\Array_) {
            $node['children'] = configTree($v, $c, $key);
        }
        // collect env() calls in the value (not in nested arrays' children which are handled recursively)
        $finder = new \PhpParser\NodeFinder();
        $calls = ($v instanceof Expr\Array_) ? [] : $finder->find($v, fn($n) => $n instanceof Expr\FuncCall && $n->name instanceof Node\Name && $n->name->toString() === 'env');
        foreach ($calls as $call) {
            $arg0 = $call->args[0] ?? null; $arg1 = $call->args[1] ?? null;
            if ($arg0 instanceof Node\Arg && $arg0->value instanceof Scalar\String_) {
                $node['envs'][] = ['env' => $arg0->value->value, 'default' => ($arg1 instanceof Node\Arg) ? literal($arg1->value) : null, 'line' => $call->getStartLine()];
            }
        }
        if (!($v instanceof Expr\Array_)) {
            $node['literal'] = literal($v);
            if ($node['literal'] === null && $calls && count($calls) === 1) {
                $d = $node['envs'][0]['default'] ?? null; if (is_string($d)) $node['default_literal'] = $d;
            }
            $cfg = $finder->find($v, fn($n) => $n instanceof Expr\FuncCall && $n->name instanceof Node\Name && $n->name->toString() === 'config');
            foreach ($cfg as $call) {
                $a0 = $call->args[0] ?? null;
                if ($a0 instanceof Node\Arg && $a0->value instanceof Scalar\String_) $node['config_refs'][] = $a0->value->value;
            }
        }
        $out[] = $node;
    }
    return $out;
}

/** routes/*.php: Route facade chains with group context (prefix/middleware/name/controller). */
function routeChain(Expr $e): ?array {
    // returns list of [name, args, line] from root Route:: static call outward
    $chain = [];
    while ($e instanceof Expr\MethodCall) { array_unshift($chain, [nameStr($e->name), $e->args, $e->getStartLine()]); $e = $e->var; }
    if ($e instanceof Expr\StaticCall && nameStr($e->class) !== null && preg_match('/(^|\\\\)Route$/', nameStr($e->class))) {
        array_unshift($chain, [nameStr($e->name), $e->args, $e->getStartLine()]);
        return $chain;
    }
    return null;
}
function strList($e): array {
    if ($e instanceof Node\Arg) $e = $e->value;
    $l = literal($e);
    if (is_string($l)) return [$l];
    if (is_array($l)) return array_values(array_filter(array_map(fn($x) => is_string($x) ? $x : (is_array($x) && isset($x['__class__']) ? $x['__class__'] : null), $l)));
    if ($e instanceof Expr\ClassConstFetch) return [ltrim(nameStr($e->class), '\\')];
    return [];
}
function routeWalk(array $stmts, array $ctx, Ctx $c): array {
    $routes = [];
    $verbs = ['get','post','put','patch','delete','options','any','match'];
    foreach ($stmts as $s) {
        if (!($s instanceof Stmt\Expression)) continue;
        $chain = routeChain($s->expr);
        if (!$chain) continue;
        $local = $ctx; $route = null; $groupBody = null; $resource = null; $localName = ''; $localMw = []; $wheres = [];
        foreach ($chain as [$m, $args, $line]) {
            $lm = strtolower((string)$m);
            if ($lm === 'prefix') { $p = strList($args[0] ?? null)[0] ?? ''; $local['prefix'] = trim($local['prefix'] . '/' . trim($p, '/'), '/'); }
            elseif ($lm === 'middleware') { $mw = []; foreach ($args as $a) $mw = array_merge($mw, strList($a)); if ($route || $resource) $localMw = array_merge($localMw, $mw); else $local['middleware'] = array_merge($local['middleware'], $mw); }
            elseif ($lm === 'withoutmiddleware') { /* recorded as-is */ $local['without_middleware'] = strList($args[0] ?? null); }
            elseif ($lm === 'name' || $lm === 'as') { $n = strList($args[0] ?? null)[0] ?? ''; if ($route || $resource) $localName .= $n; else $local['name'] .= $n; }
            elseif ($lm === 'controller') { $local['controller'] = strList($args[0] ?? null)[0] ?? null; }
            elseif ($lm === 'group') {
                $a0 = $args[0] ?? null; $a1 = $args[1] ?? null;
                if ($a0 instanceof Node\Arg && $a0->value instanceof Expr\Array_ && $a1) {
                    $opts = literal($a0->value) ?: [];
                    if (isset($opts['prefix'])) $local['prefix'] = trim($local['prefix'] . '/' . trim((string)$opts['prefix'], '/'), '/');
                    if (isset($opts['middleware'])) $local['middleware'] = array_merge($local['middleware'], (array)$opts['middleware']);
                    if (isset($opts['as'])) $local['name'] .= $opts['as'];
                    $a0 = $a1;
                }
                if ($a0 instanceof Node\Arg && ($a0->value instanceof Expr\Closure)) $groupBody = $a0->value->stmts;
            }
            elseif (in_array($lm, $verbs, true)) {
                $methods = $lm === 'match' ? strList($args[0] ?? null) : [strtoupper($lm)];
                $uriArg = $lm === 'match' ? ($args[1] ?? null) : ($args[0] ?? null);
                $actArg = $lm === 'match' ? ($args[2] ?? null) : ($args[1] ?? null);
                $uri = strList($uriArg)[0] ?? '?';
                $action = null;
                if ($actArg instanceof Node\Arg) {
                    $v = $actArg->value;
                    if ($v instanceof Expr\Array_ && count($v->items) === 2) {
                        $cls = $v->items[0]->value; $mth = $v->items[1]->value;
                        $action = ['class' => ($cls instanceof Expr\ClassConstFetch) ? ltrim(nameStr($cls->class), '\\') : null, 'method' => literal($mth)];
                    } elseif ($v instanceof Scalar\String_) {
                        if (str_contains($v->value, '@')) { [$cl, $mt] = explode('@', $v->value, 2); $action = ['class' => $cl, 'method' => $mt]; }
                        elseif ($local['controller']) $action = ['class' => $local['controller'], 'method' => $v->value];
                    } elseif ($v instanceof Expr\ClassConstFetch) {
                        $action = ['class' => ltrim(nameStr($v->class), '\\'), 'method' => '__invoke'];
                    } elseif ($v instanceof Expr\Closure || $v instanceof Expr\ArrowFunction) {
                        $action = ['closure' => true];
                    }
                }
                $route = ['methods' => $methods, 'uri' => $uri, 'action' => $action, 'line' => $line];
            }
            elseif ($lm === 'apiresource' || $lm === 'resource') {
                $resource = ['name' => strList($args[0] ?? null)[0] ?? '?', 'class' => strList($args[1] ?? null)[0] ?? null, 'api' => $lm === 'apiresource', 'line' => $line, 'only' => null, 'except' => null];
            }
            elseif ($lm === 'only' && $resource) { $resource['only'] = strList($args[0] ?? null); }
            elseif ($lm === 'except' && $resource) { $resource['except'] = strList($args[0] ?? null); }
            elseif ($lm === 'where' || $lm === 'wherenumber' || $lm === 'whereuuid') { }
        }
        if ($route) {
            $route['prefix'] = $local['prefix'];
            $route['full_uri'] = '/' . trim($local['prefix'] . '/' . trim($route['uri'], '/'), '/');
            $route['middleware'] = array_values(array_unique(array_merge($local['middleware'], $localMw)));
            $route['name'] = $local['name'] . $localName;
            $routes[] = $route;
        } elseif ($resource) {
            $acts = $resource['api'] ? ['index'=>['GET',''], 'store'=>['POST',''], 'show'=>['GET','/{id}'], 'update'=>['PUT','/{id}'], 'destroy'=>['DELETE','/{id}']]
                : ['index'=>['GET',''], 'create'=>['GET','/create'], 'store'=>['POST',''], 'show'=>['GET','/{id}'], 'edit'=>['GET','/{id}/edit'], 'update'=>['PUT','/{id}'], 'destroy'=>['DELETE','/{id}']];
            foreach ($acts as $a => [$verb, $suffix]) {
                if ($resource['only'] && !in_array($a, $resource['only'], true)) continue;
                if ($resource['except'] && in_array($a, $resource['except'], true)) continue;
                $uri = trim($resource['name'], '/') . $suffix;
                $routes[] = ['methods' => [$verb], 'uri' => $uri, 'action' => ['class' => $resource['class'], 'method' => $a], 'line' => $resource['line'],
                    'prefix' => $local['prefix'], 'full_uri' => '/' . trim($local['prefix'] . '/' . $uri, '/'),
                    'middleware' => array_values(array_unique(array_merge($local['middleware'], $localMw))), 'name' => $local['name'] . $resource['name'] . '.' . $a, 'resource' => true];
            }
        }
        if ($groupBody !== null) {
            $routes = array_merge($routes, routeWalk($groupBody, $local, $c));
        }
    }
    return $routes;
}


/* ---------------- control skeleton (for deterministic guard/gating analysis) ----------------
 * A compact tree of the statements/expressions that matter for branch evaluation. Every node
 * carries r=[lo,hi): the range of fact indices emitted inside it, so the Python evaluator can
 * mark whole regions dead under an assumption (e.g. "the new-inventory flag is on").
 */
function rng($n, Ctx $c): array { return $c->ranges[spl_object_id($n)] ?? [0, 0]; }

$GLOBALS['branchyMemo'] = [];
function branchy($e): bool {
    $memo = &$GLOBALS['branchyMemo'];
    if (!($e instanceof Node)) return false;
    $id = spl_object_id($e);
    if (isset($memo[$id])) return $memo[$id];
    $r = false;
    if ($e instanceof Expr\Ternary || $e instanceof Expr\BinaryOp\BooleanAnd || $e instanceof Expr\BinaryOp\BooleanOr
        || $e instanceof Expr\BinaryOp\LogicalAnd || $e instanceof Expr\BinaryOp\LogicalOr || $e instanceof Expr\BinaryOp\Coalesce
        || $e instanceof Expr\Match_ || ($e instanceof Expr\Assign && $e->var instanceof Expr\Variable)
        || $e instanceof Expr\Closure || $e instanceof Expr\ArrowFunction) {
        $r = true;
    } else {
        foreach ($e->getSubNodeNames() as $sn) {
            $v = $e->$sn;
            foreach ((is_array($v) ? $v : [$v]) as $x) { if (branchy($x)) { $r = true; break 2; } }
        }
    }
    return $memo[$id] = $r;
}

function skelArgs(array $args, Ctx $c): array {
    $o = [];
    foreach ($args as $a) { $o[] = ($a instanceof Node\Arg) ? skelExpr($a->value, $c) : ['k' => '?']; }
    return $o;
}

function skelExpr($e, Ctx $c): array {
    if ($e === null) return ['k' => 'lit', 'v' => null];
    $r = rng($e, $c);
    if ($e instanceof Scalar\String_ || $e instanceof Scalar\Int_ || $e instanceof Scalar\Float_) return ['k' => 'lit', 'v' => $e->value, 'r' => $r];
    if ($e instanceof Expr\ConstFetch) {
        $n = strtolower($e->name->toString());
        if ($n === 'true') return ['k' => 'lit', 'v' => true, 'r' => $r];
        if ($n === 'false') return ['k' => 'lit', 'v' => false, 'r' => $r];
        if ($n === 'null') return ['k' => 'lit', 'v' => null, 'r' => $r];
        return ['k' => '?', 'r' => $r];
    }
    if ($e instanceof Expr\ClassConstFetch && nameStr($e->name) !== 'class') return ['k' => 'const', 'class' => resolveSpecial(nameStr($e->class), $c), 'n' => nameStr($e->name), 'r' => $r];
    if ($e instanceof Expr\Variable) return is_string($e->name) ? ['k' => 'var', 'n' => $e->name, 'r' => $r] : ['k' => '?', 'r' => $r];
    if ($e instanceof Expr\BooleanNot) return ['k' => 'not', 'x' => skelExpr($e->expr, $c), 'r' => $r];
    if ($e instanceof Expr\BinaryOp\BooleanAnd || $e instanceof Expr\BinaryOp\LogicalAnd) return ['k' => 'and', 'l' => skelExpr($e->left, $c), 'rr' => skelExpr($e->right, $c), 'r' => $r];
    if ($e instanceof Expr\BinaryOp\BooleanOr || $e instanceof Expr\BinaryOp\LogicalOr) return ['k' => 'or', 'l' => skelExpr($e->left, $c), 'rr' => skelExpr($e->right, $c), 'r' => $r];
    if ($e instanceof Expr\BinaryOp\Identical || $e instanceof Expr\BinaryOp\NotIdentical || $e instanceof Expr\BinaryOp\Equal || $e instanceof Expr\BinaryOp\NotEqual) {
        return ['k' => 'cmp', 'op' => $e->getOperatorSigil(), 'l' => skelExpr($e->left, $c), 'rr' => skelExpr($e->right, $c), 'r' => $r];
    }
    if ($e instanceof Expr\BinaryOp\Coalesce) return ['k' => 'coal', 'l' => skelExpr($e->left, $c), 'rr' => skelExpr($e->right, $c), 'r' => $r];
    if ($e instanceof Expr\Ternary) return ['k' => 'tern', 'c' => skelExpr($e->cond, $c), 't' => $e->if ? skelExpr($e->if, $c) : null, 'e' => skelExpr($e->else, $c), 'r' => $r];
    if ($e instanceof Expr\Assign && $e->var instanceof Expr\Variable && is_string($e->var->name)) return ['k' => 'asg', 'n' => $e->var->name, 'x' => skelExpr($e->expr, $c), 'r' => $r];
    if ($e instanceof Expr\Cast\Bool_) return ['k' => 'cast', 'to' => 'bool', 'x' => skelExpr($e->expr, $c), 'r' => $r];
    if ($e instanceof Expr\Cast) return ['k' => 'cast', 'to' => 'other', 'x' => skelExpr($e->expr, $c), 'r' => $r];
    if ($e instanceof Expr\Isset_) return ['k' => '?', 'r' => $r];
    if ($e instanceof Expr\Array_) {
        $o = ['k' => 'arr', 'empty' => count($e->items) === 0, 'r' => $r];
        if (branchy($e)) { $o['ch'] = []; foreach ($e->items as $it) if ($it) $o['ch'][] = skelExpr($it->value, $c); }
        return $o;
    }
    if ($e instanceof Expr\New_) return ['k' => 'new', 'args' => skelArgs($e->args, $c), 'r' => $r];
    if ($e instanceof Expr\Match_) {
        $arms = [];
        foreach ($e->arms as $a) {
            $arms[] = ['conds' => $a->conds === null ? null : array_map(fn($x) => skelExpr($x, $c), $a->conds), 'body' => skelExpr($a->body, $c), 'r' => rng($a->body, $c)];
        }
        return ['k' => 'match', 'c' => skelExpr($e->cond, $c), 'arms' => $arms, 'r' => $r];
    }
    if ($e instanceof Expr\Throw_ || $e instanceof Expr\Exit_) return ['k' => 'term', 'r' => $r];
    if ($e instanceof Expr\Closure) {
        $ps = []; foreach ($e->params as $p) if ($p->var instanceof Expr\Variable && is_string($p->var->name)) $ps[] = $p->var->name;
        return ['k' => 'clo', 'params' => $ps, 'b' => skelStmts($e->stmts, $c), 'r' => $r];
    }
    if ($e instanceof Expr\ArrowFunction) {
        $ps = []; foreach ($e->params as $p) if ($p->var instanceof Expr\Variable && is_string($p->var->name)) $ps[] = $p->var->name;
        return ['k' => 'clo', 'params' => $ps, 'x' => skelExpr($e->expr, $c), 'r' => $r];
    }
    if ($e instanceof Expr\MethodCall || $e instanceof Expr\NullsafeMethodCall || $e instanceof Expr\StaticCall || $e instanceof Expr\FuncCall) {
        $o = ['k' => 'call', 'f' => $c->nodeFact[spl_object_id($e)] ?? null, 'args' => skelArgs($e->args, $c), 'r' => $r];
        if ($e instanceof Expr\FuncCall && $e->name instanceof Node\Name) $o['fn'] = strtolower($e->name->toString());
        if (($e instanceof Expr\MethodCall || $e instanceof Expr\NullsafeMethodCall)) {
            $o['m'] = nameStr($e->name);
            if (branchy($e->var) || $e->var instanceof Expr\MethodCall || $e->var instanceof Expr\FuncCall || $e->var instanceof Expr\StaticCall) $o['recv'] = skelExpr($e->var, $c);
        }
        return $o;
    }
    // anything else: keep children only if they contain branching
    $o = ['k' => '?', 'r' => $r];
    if (branchy($e)) {
        $o['ch'] = [];
        foreach ($e->getSubNodeNames() as $sn) {
            $v = $e->$sn;
            foreach ((is_array($v) ? $v : [$v]) as $x) {
                if ($x instanceof Node\Arg) $x = $x->value;
                if ($x instanceof Expr) $o['ch'][] = skelExpr($x, $c);
            }
        }
    }
    return $o;
}

/** Variables assigned anywhere in a subtree (for havoc at loop/branch joins). */
function assignedVars($n, array &$out): void {
    if (is_array($n)) { foreach ($n as $x) assignedVars($x, $out); return; }
    if (!($n instanceof Node) || $n instanceof Expr\Closure || $n instanceof Expr\ArrowFunction) return;
    if (($n instanceof Expr\Assign || $n instanceof Expr\AssignOp || $n instanceof Expr\AssignRef) && $n->var instanceof Expr\Variable && is_string($n->var->name)) $out[$n->var->name] = true;
    if ($n instanceof Stmt\Foreach_) {
        foreach ([$n->keyVar, $n->valueVar] as $v) if ($v instanceof Expr\Variable && is_string($v->name)) $out[$v->name] = true;
    }
    foreach ($n->getSubNodeNames() as $sn) assignedVars($n->$sn, $out);
}

function skelStmts(array $stmts, Ctx $c): array {
    $o = [];
    foreach ($stmts as $s) $o[] = skelStmt($s, $c);
    return $o;
}

function skelBlock(string $kind, Node $s, array $pre, array $blocks, Ctx $c): array {
    $av = []; assignedVars($blocks, $av);
    return ['k' => 'blk', 'kind' => $kind, 'pre' => $pre, 'b' => array_map(fn($b) => skelStmts($b, $c), $blocks), 'av' => array_keys($av), 'r' => rng($s, $c)];
}

function skelStmt($s, Ctx $c): array {
    $r = rng($s, $c);
    if ($s instanceof Stmt\If_) {
        $ei = [];
        foreach ($s->elseifs as $x) $ei[] = ['c' => skelExpr($x->cond, $c), 'b' => skelStmts($x->stmts, $c), 'r' => rng($x, $c), 'line' => $x->getStartLine()];
        return ['k' => 'if', 'c' => skelExpr($s->cond, $c), 'line' => $s->getStartLine(), 't' => skelStmts($s->stmts, $c), 'tr' => $s->stmts ? [rng($s->stmts[0], $c)[0], rng(end($s->stmts), $c)[1]] : [0, 0],
            'ei' => $ei, 'e' => $s->else ? skelStmts($s->else->stmts, $c) : null, 'er' => $s->else ? rng($s->else, $c) : null, 'r' => $r];
    }
    if ($s instanceof Stmt\Return_) return ['k' => 'ret', 'x' => $s->expr ? skelExpr($s->expr, $c) : null, 'line' => $s->getStartLine(), 'r' => $r];
    if ($s instanceof Stmt\Continue_ || $s instanceof Stmt\Break_) return ['k' => 'brk', 'r' => $r];
    if ($s instanceof Stmt\Expression) {
        $e = $s->expr;
        if ($e instanceof Expr\Throw_ || $e instanceof Expr\Exit_) return ['k' => 'term', 'r' => $r];
        return ['k' => 'x', 'x' => skelExpr($e, $c), 'r' => $r];
    }
    if ($s instanceof Stmt\Foreach_) return skelBlock('foreach', $s, [skelExpr($s->expr, $c)], [$s->stmts], $c);
    if ($s instanceof Stmt\While_) return skelBlock('while', $s, [skelExpr($s->cond, $c)], [$s->stmts], $c);
    if ($s instanceof Stmt\Do_) return skelBlock('do', $s, [], [$s->stmts], $c);
    if ($s instanceof Stmt\For_) return skelBlock('for', $s, [], [$s->stmts], $c);
    if ($s instanceof Stmt\Switch_) return skelBlock('switch', $s, [skelExpr($s->cond, $c)], array_map(fn($k) => $k->stmts, $s->cases), $c);
    if ($s instanceof Stmt\TryCatch) {
        $av = []; assignedVars(array_map(fn($k) => $k->stmts, $s->catches), $av);
        return ['k' => 'try', 'b' => skelStmts($s->stmts, $c), 'catches' => array_map(fn($k) => skelStmts($k->stmts, $c), $s->catches),
            'fin' => $s->finally ? skelStmts($s->finally->stmts, $c) : [], 'av' => array_keys($av), 'r' => $r];
    }
    if ($s instanceof Stmt\Block) return ['k' => 'seq', 'b' => skelStmts($s->stmts, $c), 'r' => $r];
    return ['k' => 'other', 'r' => $r];
}
