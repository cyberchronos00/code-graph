// Dart -> JSON facts for code-graph. Parse-only (package:analyzer unresolved AST, no pub get of the
// target project needed): declarations, directives and per-body facts (calls, instance creations, index
// reads, local vars, assignments, returns, type checks, try ranges) with source lines. Name/type
// resolution happens in Python (codegraph/plugins/dart/plugin.py).
//
//   dart run bin/extract.dart --config cfg.json     cfg: {"root": "...", "out": "...", "skip_dirs": [...]}
import 'dart:convert';
import 'dart:io';

import 'package:analyzer/dart/analysis/features.dart';
import 'package:analyzer/dart/analysis/utilities.dart';
import 'package:analyzer/dart/ast/ast.dart';
import 'package:analyzer/dart/ast/visitor.dart';
import 'package:analyzer/source/line_info.dart';

const maxDepth = 7;

// latest language + primary constructors (Dart 3.13 projects already use them)
final features = FeatureSet.latestLanguageVersion(flags: ['primary-constructors']);
final baseFeatures = FeatureSet.latestLanguageVersion();

late LineInfo li;
int ln(AstNode n) => li.getLocation(n.offset).lineNumber;
int lnEnd(AstNode n) => li.getLocation(n.end).lineNumber;

String src(AstNode? n, [int max = 160]) {
  if (n == null) return '';
  final s = n.toSource();
  return s.length > max ? '${s.substring(0, max)}…' : s;
}

String typeText(TypeAnnotation? t) => t == null ? '' : t.toSource();

// conditional imports / exports: `import 'stub.dart' if (dart.library.io) 'io.dart'`
List<Map<String, dynamic>> configs(NodeList<Configuration> cs) => [
      for (final c in cs)
        {'name': c.name.toSource(), if (c.value != null) 'value': c.value!.stringValue, 'uri': c.uri.stringValue}
    ];

Map<String, dynamic>? repr(Expression? e, [int d = 0]) {
  if (e == null) return null;
  if (d > maxDepth) return {'k': 'other', 'v': src(e, 60)};
  final n = d + 1;
  if (e is SimpleStringLiteral) return {'k': 'str', 'v': e.value};
  if (e is StringInterpolation) {
    final parts = <dynamic>[];
    for (final el in e.elements) {
      if (el is InterpolationString) {
        if (el.value.isNotEmpty) parts.add(el.value);
      } else if (el is InterpolationExpression) {
        parts.add({'e': repr(el.expression, n)});
      }
    }
    return {'k': 'tpl', 'parts': parts};
  }
  if (e is AdjacentStrings) return {'k': 'cat', 'parts': [for (final s in e.strings) repr(s, n)]};
  if (e is IntegerLiteral) return {'k': 'num', 'v': e.value};
  if (e is DoubleLiteral) return {'k': 'num', 'v': e.value};
  if (e is BooleanLiteral) return {'k': 'bool', 'v': e.value};
  if (e is NullLiteral) return {'k': 'null'};
  if (e is SimpleIdentifier) return {'k': 'id', 'v': e.name};
  if (e is PrefixedIdentifier) return {'k': 'prop', 't': {'k': 'id', 'v': e.prefix.name}, 'n': e.identifier.name};
  if (e is PropertyAccess) {
    return {'k': 'prop', 't': e.isCascaded ? {'k': 'cascade_t'} : repr(e.target, n), 'n': e.propertyName.name,
      if (e.isNullAware) 'qa': true};
  }
  if (e is ThisExpression) return {'k': 'this'};
  if (e is SuperExpression) return {'k': 'super'};
  if (e is MethodInvocation) return callRepr(e, n);
  if (e is InstanceCreationExpression) return newRepr(e, n);
  if (e is FunctionExpressionInvocation) {
    return {'k': 'call', 't': repr(e.function, n), 'n': null, 'a': args(e.argumentList, n), 'na': named(e.argumentList, n), 'l': ln(e)};
  }
  if (e is IndexExpression) return {'k': 'idx', 't': e.isCascaded ? {'k': 'cascade_t'} : repr(e.target, n), 'i': repr(e.index, n), 'l': ln(e)};
  if (e is AsExpression) return {'k': 'as', 'e': repr(e.expression, n), 'type': typeText(e.type)};
  if (e is ParenthesizedExpression) return repr(e.expression, d);
  if (e is AwaitExpression) return {'k': 'await', 'e': repr(e.expression, n)};
  if (e is PostfixExpression) return {'k': 'nn', 'e': repr(e.operand, n), 'op': e.operator.lexeme};
  if (e is ConditionalExpression) return {'k': 'cond', 'c': src(e.condition, 80), 'a': repr(e.thenExpression, n), 'b': repr(e.elseExpression, n)};
  if (e is BinaryExpression) return {'k': 'bin', 'op': e.operator.lexeme, 'l': repr(e.leftOperand, n), 'r': repr(e.rightOperand, n)};
  if (e is SetOrMapLiteral) {
    if (e.isMap || (e.elements.isNotEmpty && e.elements.first is MapLiteralEntry) || (e.elements.isEmpty && !e.isSet)) {
      return {'k': 'map', 'entries': [for (final el in e.elements.take(60)) elemRepr(el, n)], 'l': ln(e)};
    }
    return {'k': 'list', 'items': [for (final el in e.elements.take(30)) elemRepr(el, n)]};
  }
  if (e is ListLiteral) return {'k': 'list', 'items': [for (final el in e.elements.take(30)) elemRepr(el, n)], 'l': ln(e)};
  if (e is FunctionExpression) return fnRepr(e, n);
  if (e is CascadeExpression) {
    return {'k': 'cascade', 't': repr(e.target, n), 'sections': [for (final s in e.cascadeSections.take(20)) repr(s, n)]};
  }
  if (e is AssignmentExpression) return {'k': 'assign', 'lhs': repr(e.leftHandSide, n), 'rhs': repr(e.rightHandSide, n)};
  if (e is TypeLiteral) return {'k': 'id', 'v': e.type.toSource()};
  if (e is NamedExpression) return repr(e.expression, d);
  if (e is SwitchExpression) {
    return {'k': 'switch', 'e': repr(e.expression, n), 'cases': [for (final c in e.cases.take(30)) {'p': src(c.guardedPattern, 80), 'v': repr(c.expression, n)}]};
  }
  return {'k': 'other', 'v': src(e, 80)};
}

dynamic elemRepr(CollectionElement el, int d) {
  if (el is MapLiteralEntry) return {'key': repr(el.key, d), 'value': repr(el.value, d), 'l': ln(el)};
  if (el is Expression) return repr(el, d);
  if (el is SpreadElement) return {'k': 'spread', 'e': repr(el.expression, d)};
  if (el is IfElement) return {'k': 'if', 'c': src(el.expression, 60), 'then': elemRepr(el.thenElement, d), if (el.elseElement != null) 'else': elemRepr(el.elseElement!, d)};
  if (el is ForElement) return {'k': 'for', 'body': elemRepr(el.body, d)};
  return {'k': 'other', 'v': src(el, 60)};
}

List<dynamic> args(ArgumentList al, int d) => [for (final a in al.arguments.where((a) => a is! NamedExpression).take(12)) repr(a, d)];
Map<String, dynamic> named(ArgumentList al, int d) => {
      for (final a in al.arguments.whereType<NamedExpression>().take(30)) a.name.label.name: repr(a.expression, d)
    };

Map<String, dynamic> callRepr(MethodInvocation e, int d) => {
      'k': 'call',
      't': e.isCascaded ? {'k': 'cascade_t'} : repr(e.target, d),
      'n': e.methodName.name,
      if (e.typeArguments != null) 'ta': [for (final t in e.typeArguments!.arguments) t.toSource()],
      'a': args(e.argumentList, d),
      'na': named(e.argumentList, d),
      if (e.isNullAware) 'qa': true,
      'l': ln(e),
    };

Map<String, dynamic> newRepr(InstanceCreationExpression e, int d) {
  final cn = e.constructorName;
  final nt = cn.type;
  final prefix = nt.importPrefix?.name.lexeme;
  return {
    'k': 'new',
    'type': prefix != null ? '$prefix.${nt.name.lexeme}' : nt.name.lexeme,
    if (cn.name != null) 'ctor': cn.name!.name,
    if (nt.typeArguments != null) 'ta': [for (final t in nt.typeArguments!.arguments) t.toSource()],
    'a': args(e.argumentList, d),
    'na': named(e.argumentList, d),
    'l': ln(e),
  };
}

Map<String, dynamic> fnRepr(FunctionExpression e, int d) {
  final body = e.body;
  final out = <String, dynamic>{
    'k': 'fn',
    'params': [for (final p in e.parameters?.parameters ?? <FormalParameter>[]) p.name?.lexeme ?? '_'],
    'l': ln(e),
  };
  if (body is ExpressionFunctionBody) {
    out['ret'] = [repr(body.expression, d)];
  } else if (body is BlockFunctionBody) {
    final rets = <dynamic>[];
    body.block.accept(_ReturnCollector(rets, d));
    out['ret'] = rets.take(8).toList();
  }
  return out;
}

class _ReturnCollector extends RecursiveAstVisitor<void> {
  final List<dynamic> rets;
  final int d;
  _ReturnCollector(this.rets, this.d);
  @override
  void visitReturnStatement(ReturnStatement node) {
    if (node.expression != null) rets.add(repr(node.expression, d));
  }

  @override
  void visitFunctionExpression(FunctionExpression node) {} // returns of nested closures are theirs
}

List<Map<String, dynamic>> annotations(NodeList<Annotation> md) => [
      for (final a in md)
        {
          'name': a.name.toSource() + (a.constructorName != null ? '.${a.constructorName!.name}' : ''),
          if (a.arguments != null) 'a': args(a.arguments!, 1),
          if (a.arguments != null) 'na': named(a.arguments!, 1),
          'l': ln(a),
        }
    ];

List<ClassMember> bodyMembers(ClassBody b) => b is BlockClassBody ? b.members.toList() : <ClassMember>[];

Map<String, dynamic> param(FormalParameter p) {
  FormalParameter base = p;
  Expression? def;
  if (p is DefaultFormalParameter) {
    def = p.defaultValue;
    base = p.parameter;
  }
  String type = '';
  bool isThis = false, isSuper = false;
  if (base is SimpleFormalParameter) type = typeText(base.type);
  if (base is FieldFormalParameter) {
    type = typeText(base.type);
    isThis = true;
  }
  if (base is SuperFormalParameter) {
    type = typeText(base.type);
    isSuper = true;
  }
  if (base is FunctionTypedFormalParameter) type = 'Function';
  return {
    'name': p.name?.lexeme ?? '_',
    if (type.isNotEmpty) 'type': type,
    if (p.isNamed) 'named': true,
    if (p.isRequired) 'required': true,
    if (isThis) 'this': true,
    if (isSuper) 'super': true,
    if (def != null) 'default': repr(def),
    if (p.metadata.isNotEmpty) 'ann': annotations(p.metadata),
  };
}

bool enumLike(Expression e) {
  if (e is PrefixedIdentifier) return RegExp(r'^[A-Z]').hasMatch(e.prefix.name);
  if (e is PropertyAccess) {
    final t = e.target;
    return t is SimpleIdentifier && RegExp(r'^[A-Z]').hasMatch(t.name) || t is PrefixedIdentifier && RegExp(r'^[A-Z]').hasMatch(t.identifier.name);
  }
  return false;
}

/// Facts inside one executable body. `on<E>(closure)` handlers inside a bloc constructor are split out
/// into their own synthetic member so each event's handler has its own calls.
class BodyFacts extends RecursiveAstVisitor<void> {
  final List<Map<String, dynamic>> facts = [];
  final List<Map<String, dynamic>> extraMembers;
  BodyFacts(this.extraMembers);

  @override
  void visitMethodInvocation(MethodInvocation node) {
    if (node.methodName.name == 'on' && node.target == null && node.typeArguments != null &&
        node.argumentList.arguments.isNotEmpty && node.argumentList.arguments.first is FunctionExpression) {
      final fe = node.argumentList.arguments.first as FunctionExpression;
      final sub = BodyFacts(extraMembers);
      fe.body.accept(sub);
      final ev = node.typeArguments!.arguments.first.toSource();
      facts.add({'t': 'call', ...callRepr(node, 1)});
      extraMembers.add({
        'name': 'on<$ev>@${ln(node)}',
        'kind': 'handler',
        'event': ev,
        'params': [for (final p in fe.parameters?.parameters ?? <FormalParameter>[]) param(p)],
        'line': ln(node),
        'end': lnEnd(node),
        'facts': sub.facts,
      });
      for (final a in node.argumentList.arguments.skip(1)) {
        a.accept(this);
      }
      return;
    }
    facts.add({'t': 'call', ...callRepr(node, 1)});
    super.visitMethodInvocation(node);
  }

  @override
  void visitInstanceCreationExpression(InstanceCreationExpression node) {
    facts.add({'t': 'new', ...newRepr(node, 1)});
    super.visitInstanceCreationExpression(node);
  }

  @override
  void visitFunctionExpressionInvocation(FunctionExpressionInvocation node) {
    facts.add({'t': 'call', ...repr(node, 1)!});
    super.visitFunctionExpressionInvocation(node);
  }

  @override
  void visitIndexExpression(IndexExpression node) {
    final key = node.index;
    if (key is SimpleStringLiteral || key is StringInterpolation) {
      final f = <String, dynamic>{'t': 'index', 'target': node.isCascaded ? {'k': 'cascade_t'} : repr(node.target, 1), 'key': repr(key, 1), 'l': ln(node)};
      AstNode p = node.parent!;
      while (p is ParenthesizedExpression) {
        p = p.parent!;
      }
      if (p is AsExpression) {
        f['cast'] = typeText(p.type);
        AstNode q = p.parent!;
        while (q is ParenthesizedExpression) {
          q = q.parent!;
        }
        if (q is MethodInvocation && q.target != null) f['post'] = q.methodName.name;
        if (q is PropertyAccess) f['post'] = q.propertyName.name;
      } else if (p is MethodInvocation && p.target == node) {
        f['post'] = p.methodName.name;
      } else if (p is PropertyAccess && p.target == node) {
        f['post'] = p.propertyName.name;
      } else if (p is ArgumentList && p.parent is MethodInvocation) {
        final mi = p.parent as MethodInvocation;
        f['wrap'] = mi.target != null ? '${src(mi.target, 40)}.${mi.methodName.name}' : mi.methodName.name;
      } else if (p is ArgumentList && p.parent is InstanceCreationExpression) {
        f['wrap'] = src((p.parent as InstanceCreationExpression).constructorName, 40);
      } else if (p is BinaryExpression && p.operator.lexeme == '??' && p.leftOperand == node) {
        f['coalesce'] = true;
      } else if (p is AssignmentExpression && p.leftHandSide == node) {
        f['write'] = true;
      }
      if (p is BinaryExpression && p.operator.lexeme == '??') f['coalesce'] = true;
      facts.add(f);
    }
    super.visitIndexExpression(node);
  }

  @override
  void visitVariableDeclaration(VariableDeclaration node) {
    final list = node.parent;
    facts.add({
      't': 'var',
      'name': node.name.lexeme,
      if (list is VariableDeclarationList && list.type != null) 'type': typeText(list.type),
      if (node.initializer != null) 'init': repr(node.initializer),
      'l': ln(node),
    });
    super.visitVariableDeclaration(node);
  }

  @override
  void visitAssignmentExpression(AssignmentExpression node) {
    facts.add({'t': 'assign', 'lhs': repr(node.leftHandSide, 1), 'rhs': repr(node.rightHandSide, 1), 'op': node.operator.lexeme, 'l': ln(node)});
    super.visitAssignmentExpression(node);
  }

  @override
  void visitReturnStatement(ReturnStatement node) {
    if (node.expression != null) facts.add({'t': 'return', 'v': repr(node.expression, 1), 'l': ln(node)});
    super.visitReturnStatement(node);
  }

  @override
  void visitExpressionFunctionBody(ExpressionFunctionBody node) {
    if (node.parent is! FunctionExpression || node.parent!.parent is FunctionDeclaration) {
      facts.add({'t': 'return', 'v': repr(node.expression, 1), 'l': ln(node)});
    }
    super.visitExpressionFunctionBody(node);
  }

  @override
  void visitIsExpression(IsExpression node) {
    facts.add({'t': 'is', 'type': typeText(node.type), 'e': src(node.expression, 60), if (node.notOperator != null) 'not': true, 'l': ln(node)});
    super.visitIsExpression(node);
  }

  @override
  void visitObjectPattern(ObjectPattern node) {
    facts.add({'t': 'is', 'type': node.type.toSource(), 'e': 'pattern', 'l': ln(node)});
    super.visitObjectPattern(node);
  }

  @override
  void visitDeclaredVariablePattern(DeclaredVariablePattern node) {
    if (node.type != null) facts.add({'t': 'is', 'type': typeText(node.type), 'e': 'pattern', 'l': ln(node)});
    super.visitDeclaredVariablePattern(node);
  }

  @override
  void visitTryStatement(TryStatement node) {
    facts.add({
      't': 'try',
      'l': ln(node),
      'end': lnEnd(node.body),
      'catches': [for (final c in node.catchClauses) c.exceptionType?.toSource() ?? '*'],
      if (node.finallyBlock != null) 'finally': true,
    });
    super.visitTryStatement(node);
  }

  @override
  void visitBinaryExpression(BinaryExpression node) {
    const ops = {'==', '!=', '>=', '<=', '<', '>'};
    if (ops.contains(node.operator.lexeme)) {
      final l = node.leftOperand, r = node.rightOperand;
      Expression? side;
      IntegerLiteral? lit;
      if (r is IntegerLiteral) {
        side = l;
        lit = r;
      } else if (l is IntegerLiteral) {
        side = r;
        lit = l;
      }
      if ((node.operator.lexeme == '==' || node.operator.lexeme == '!=') && (enumLike(l) || enumLike(r))) {
        facts.add({'t': 'cmp', 'a': src(l, 80), 'b': src(r, 80), 'op': node.operator.lexeme, 'l': ln(node)});
      }
      if (side != null && lit != null && RegExp('(statusCode|status)[\'"]?\\]?\$').hasMatch(src(side, 80))) {
        facts.add({'t': 'status', 'e': src(side, 60), 'op': node.operator.lexeme, 'v': lit.value, 'lit_left': l is IntegerLiteral, 'l': ln(node)});
      }
    }
    super.visitBinaryExpression(node);
  }

  @override
  void visitSwitchStatement(SwitchStatement node) {
    facts.add({
      't': 'switch',
      'e': src(node.expression, 80),
      'cases': [
        for (final m in node.members)
          if (m is SwitchPatternCase) src(m.guardedPattern, 80) else if (m is SwitchCase) src(m.expression, 80) else 'default'
      ],
      'l': ln(node)
    });
    super.visitSwitchStatement(node);
  }

  @override
  void visitSwitchExpression(SwitchExpression node) {
    facts.add({'t': 'switch', 'e': src(node.expression, 80), 'cases': [for (final c in node.cases) src(c.guardedPattern, 80)], 'l': ln(node)});
    super.visitSwitchExpression(node);
  }

  @override
  void visitThrowExpression(ThrowExpression node) {
    facts.add({'t': 'throw', 'v': repr(node.expression, 1), 'l': ln(node)});
    super.visitThrowExpression(node);
  }
}

Map<String, dynamic> member(ClassMember m, List<Map<String, dynamic>> extra) {
  if (m is FieldDeclaration) {
    return {
      'kind': 'field',
      if (m.isStatic) 'static': true,
      if (m.fields.isFinal) 'final': true,
      if (m.fields.isConst) 'const': true,
      if (m.fields.isLate) 'late': true,
      if (m.fields.type != null) 'type': typeText(m.fields.type),
      'vars': [
        for (final v in m.fields.variables)
          {'name': v.name.lexeme, if (v.initializer != null) 'init': repr(v.initializer), 'l': ln(v)}
      ],
      'ann': annotations(m.metadata),
      'line': ln(m),
    };
  }
  final bf = BodyFacts(extra);
  if (m is ConstructorDeclaration) {
    m.body.accept(bf);
    for (final i in m.initializers) {
      i.accept(bf);
    }
    for (final p in m.parameters.parameters) {
      if (p is DefaultFormalParameter && p.defaultValue != null) p.defaultValue!.accept(bf);
    }
    final inits = <Map<String, dynamic>>[];
    for (final i in m.initializers) {
      if (i is ConstructorFieldInitializer) inits.add({'field': i.fieldName.name, 'v': repr(i.expression), 'l': ln(i)});
      if (i is SuperConstructorInvocation) inits.add({'super': i.constructorName?.name ?? '', 'a': args(i.argumentList, 1), 'na': named(i.argumentList, 1), 'l': ln(i)});
    }
    return {
      'kind': 'ctor',
      'name': m.name?.lexeme ?? '',
      if (m.factoryKeyword != null) 'factory': true,
      if (m.constKeyword != null) 'const': true,
      if (m.redirectedConstructor != null) 'redirect': m.redirectedConstructor!.toSource(),
      'params': [for (final p in m.parameters.parameters) param(p)],
      'inits': inits,
      'ann': annotations(m.metadata),
      'line': ln(m),
      'end': lnEnd(m),
      'facts': bf.facts,
    };
  }
  if (m is MethodDeclaration) {
    m.body.accept(bf);
    return {
      'kind': m.isGetter ? 'getter' : (m.isSetter ? 'setter' : (m.isOperator ? 'operator' : 'method')),
      'name': m.name.lexeme,
      if (m.isStatic) 'static': true,
      if (m.isAbstract) 'abstract': true,
      if (m.returnType != null) 'ret': typeText(m.returnType),
      if (m.body.isAsynchronous) 'async': true,
      'params': [for (final p in m.parameters?.parameters ?? <FormalParameter>[]) param(p)],
      'ann': annotations(m.metadata),
      'line': ln(m),
      'end': lnEnd(m),
      'facts': bf.facts,
    };
  }
  return {'kind': 'other', 'line': ln(m)};
}

List<String> types(Iterable<NamedType>? ts) => [for (final t in ts ?? <NamedType>[]) t.toSource()];

Map<String, dynamic> fileFacts(String rel, String content) {
  var res = parseString(content: content, path: rel, throwIfDiagnostics: false, featureSet: baseFeatures);
  if (res.errors.isNotEmpty) {
    // newer syntax (primary constructors, `factory name(...)` shorthand): re-parse with the experiment and keep the
    // better parse (the experiment changes other rules, e.g. `final` params, so it is not the default)
    final res2 = parseString(content: content, path: rel, throwIfDiagnostics: false, featureSet: features);
    if (res2.errors.length < res.errors.length) res = res2;
  }
  final cu = res.unit;
  li = res.lineInfo;
  final out = <String, dynamic>{
    'file': rel,
    'errors': res.errors.length,
    if (res.errors.isNotEmpty) 'first_error': '${res.errors.first.message} @${li.getLocation(res.errors.first.offset).lineNumber}',
    if (res.errors.isNotEmpty) 'error_lines': [for (final e in res.errors.take(20)) li.getLocation(e.offset).lineNumber],
    'imports': [],
    'exports': [],
    'parts': [],
    'classes': [],
    'functions': [],
    'vars': [],
    'lines': li.lineCount,
  };
  for (final d in cu.directives) {
    if (d is ImportDirective) {
      out['imports'].add({
        'uri': d.uri.stringValue,
        if (d.prefix != null) 'prefix': d.prefix!.name,
        'show': [for (final c in d.combinators.whereType<ShowCombinator>()) for (final n in c.shownNames) n.name],
        'hide': [for (final c in d.combinators.whereType<HideCombinator>()) for (final n in c.hiddenNames) n.name],
        'l': ln(d),
        if (d.configurations.isNotEmpty) 'configs': configs(d.configurations),
      });
    } else if (d is ExportDirective) {
      out['exports'].add({
        'uri': d.uri.stringValue,
        'show': [for (final c in d.combinators.whereType<ShowCombinator>()) for (final n in c.shownNames) n.name],
        'hide': [for (final c in d.combinators.whereType<HideCombinator>()) for (final n in c.hiddenNames) n.name],
        'l': ln(d),
        if (d.configurations.isNotEmpty) 'configs': configs(d.configurations),
      });
    } else if (d is PartDirective) {
      out['parts'].add(d.uri.stringValue);
    } else if (d is PartOfDirective) {
      out['part_of'] = d.uri?.stringValue ?? d.libraryName?.toSource();
    } else if (d is LibraryDirective) {
      out['library'] = d.name?.toSource();
    }
  }
  for (final decl in cu.declarations) {
    if (decl is ClassDeclaration || decl is MixinDeclaration || decl is EnumDeclaration || decl is ExtensionDeclaration ||
        decl is ExtensionTypeDeclaration) {
      final extra = <Map<String, dynamic>>[];
      final c = <String, dynamic>{'line': ln(decl), 'end': lnEnd(decl), 'ann': annotations(decl.metadata)};
      List<ClassMember> members = [];
      if (decl is ClassDeclaration) {
        c.addAll({
          'kind': 'class',
          'name': decl.name.lexeme,
          if (decl.abstractKeyword != null) 'abstract': true,
          if (decl.sealedKeyword != null) 'sealed': true,
          if (decl.extendsClause != null) 'extends': decl.extendsClause!.superclass.toSource(),
          'with': types(decl.withClause?.mixinTypes),
          'implements': types(decl.implementsClause?.interfaces),
        });
        members = bodyMembers(decl.body);
        final np = decl.namePart;
        if (np is PrimaryConstructorDeclaration) {
          // `class C(final A a, {var B? b})`: declaring params become fields + an unnamed (or named) constructor
          final ps = np.formalParameters.parameters;
          final pc = <Map<String, dynamic>>[];
          for (final p in ps) {
            final base = p is DefaultFormalParameter ? p.parameter : p;
            final declaring = base is SimpleFormalParameter && base.keyword != null;
            final pm = param(p);
            if (declaring) {
              pm['this'] = true;
              c.putIfAbsent('pc_fields', () => <Map<String, dynamic>>[]);
              (c['pc_fields'] as List).add({
                'kind': 'field',
                if (base.keyword!.lexeme == 'final') 'final': true,
                if (base.type != null) 'type': typeText(base.type),
                'vars': [{'name': p.name?.lexeme ?? '_', 'l': ln(p)}],
                'ann': annotations(p.metadata),
                'line': ln(p),
              });
            }
            pc.add(pm);
          }
          c['pc_ctor'] = {
            'kind': 'ctor',
            'name': np.constructorName?.name.lexeme ?? '',
            if (np.constKeyword != null) 'const': true,
            'params': pc,
            'inits': <Map<String, dynamic>>[],
            'ann': <Map<String, dynamic>>[],
            'line': ln(np),
            'end': lnEnd(np),
            'facts': <Map<String, dynamic>>[],
          };
        }
      } else if (decl is MixinDeclaration) {
        c.addAll({
          'kind': 'mixin',
          'name': decl.name.lexeme,
          'on': types(decl.onClause?.superclassConstraints),
          'implements': types(decl.implementsClause?.interfaces),
        });
        members = decl.members.toList();
      } else if (decl is EnumDeclaration) {
        c.addAll({
          'kind': 'enum',
          'name': decl.name.lexeme,
          'with': types(decl.withClause?.mixinTypes),
          'implements': types(decl.implementsClause?.interfaces),
          'values': [
            for (final v in decl.constants)
              {
                'name': v.name.lexeme,
                if (v.arguments != null) 'a': args(v.arguments!.argumentList, 1),
                'ann': annotations(v.metadata),
                'l': ln(v)
              }
          ],
        });
        members = decl.members.toList();
      } else if (decl is ExtensionDeclaration) {
        c.addAll({'kind': 'extension', 'name': decl.name?.lexeme ?? '_ext${ln(decl)}', 'on_type': decl.onClause?.extendedType.toSource()});
        members = decl.members.toList();
      } else if (decl is ExtensionTypeDeclaration) {
        c.addAll({'kind': 'extension_type', 'name': decl.name.lexeme, 'implements': types(decl.implementsClause?.interfaces)});
        members = bodyMembers(decl.body);
      }
      c['members'] = [for (final m in members) member(m, extra)];
      (c['members'] as List).addAll(extra);
      if (c.containsKey('pc_fields')) (c['members'] as List).addAll(c.remove('pc_fields') as List);
      if (c.containsKey('pc_ctor')) (c['members'] as List).add(c.remove('pc_ctor'));
      out['classes'].add(c);
    } else if (decl is ClassTypeAlias) {
      out['classes'].add({
        'kind': 'class',
        'name': decl.name.lexeme,
        'extends': decl.superclass.toSource(),
        'with': types(decl.withClause.mixinTypes),
        'implements': types(decl.implementsClause?.interfaces),
        'ann': annotations(decl.metadata),
        'members': [],
        'line': ln(decl),
        'end': lnEnd(decl),
      });
    } else if (decl is FunctionDeclaration) {
      final extra = <Map<String, dynamic>>[];
      final bf = BodyFacts(extra);
      decl.functionExpression.body.accept(bf);
      out['functions'].add({
        'name': decl.name.lexeme,
        'kind': decl.isGetter ? 'getter' : 'function',
        if (decl.returnType != null) 'ret': typeText(decl.returnType),
        'params': [for (final p in decl.functionExpression.parameters?.parameters ?? <FormalParameter>[]) param(p)],
        'ann': annotations(decl.metadata),
        if (decl.functionExpression.body.isAsynchronous) 'async': true,
        'line': ln(decl),
        'end': lnEnd(decl),
        'facts': bf.facts,
      });
    } else if (decl is TopLevelVariableDeclaration) {
      for (final v in decl.variables.variables) {
        final bf = BodyFacts([]);
        v.initializer?.accept(bf);
        out['vars'].add({
          'name': v.name.lexeme,
          if (decl.variables.type != null) 'type': typeText(decl.variables.type),
          if (decl.variables.isConst) 'const': true,
          if (decl.variables.isFinal) 'final': true,
          if (v.initializer != null) 'init': repr(v.initializer),
          'l': ln(v),
          'facts': bf.facts,
        });
      }
    }
  }
  return out;
}

void main(List<String> argv) {
  final cfgPath = argv[argv.indexOf('--config') + 1];
  final cfg = jsonDecode(File(cfgPath).readAsStringSync()) as Map<String, dynamic>;
  final root = Directory(cfg['root'] as String);
  // directory rules from the presets and .cg.yaml (codegraph/core/paths.py PathRules.extractor_cfg): names to skip,
  // names kept (hidden directories are skipped unless kept), include directories walked although a rule covers them
  final skip = {...(cfg['skip_names'] as List? ?? []).cast<String>(), ...(cfg['skip_dirs'] as List? ?? []).cast<String>()};
  final keep = {...(cfg['keep_names'] as List? ?? []).cast<String>()};
  final include = (cfg['include'] as List? ?? []).cast<String>().map((i) => i.replaceAll(RegExp(r'^/+|/+$'), '')).where((i) => i.isNotEmpty).toList();
  bool included(String r) => include.any((i) => r == i || r.startsWith('$i/'));
  bool onIncludePath(String r) => included(r) || include.any((i) => i.startsWith('$r/'));
  bool skippedName(String name) => skip.contains(name) || (name.startsWith('.') && !keep.contains(name));
  final skipSuffix = (cfg['skip_suffixes'] as List? ?? ['.freezed.dart', '.mocks.dart']).cast<String>();
  final files = <Map<String, dynamic>>[];
  final failures = <Map<String, dynamic>>[];
  var skipped = 0;
  final rootPath = root.absolute.path;
  // forced: `d` is skipped by itself and only walked to reach an include directory
  void walk(Directory d, [bool forced = false]) {
    final ents = d.listSync(followLinks: false)..sort((a, b) => a.path.compareTo(b.path));
    for (final e in ents) {
      final name = e.uri.pathSegments.where((s) => s.isNotEmpty).last;
      final rel = e.absolute.path.substring(rootPath.length).replaceFirst(RegExp(r'^/'), '').replaceFirst(RegExp(r'/$'), '');
      if (e is Directory) {
        final own = forced || skippedName(name);
        if (onIncludePath(rel) || !own) walk(e, own && !included(rel));
      } else if (e is File && name.endsWith('.dart')) {
        if (forced && !included(rel)) continue;
        if (skipSuffix.any((s) => name.endsWith(s))) {
          skipped++;
          continue;
        }
        if (e.lengthSync() > 1500000) {
          skipped++;
          continue;
        }
        try {
          files.add(fileFacts(rel, e.readAsStringSync()));
        } catch (ex) {
          failures.add({'file': rel, 'error': ex.toString().split('\n').first});
        }
      }
    }
  }

  walk(root);
  File(cfg['out'] as String).writeAsStringSync(jsonEncode({'files': files, 'failures': failures, 'skipped': skipped}));
}
