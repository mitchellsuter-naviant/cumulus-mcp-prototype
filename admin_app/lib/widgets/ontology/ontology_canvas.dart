import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../models/ontology_screen.dart';
import '../../services/api_client.dart';
import '../../theme/cumulus_theme.dart';
import '../skeleton.dart';
import '../ui_kit.dart';
import 'ontology_layout.dart';

/// The whole model, on one canvas.
///
/// The old diagram drew one type and its neighbours, which answers "what
/// touches this" and nothing else — you could not see the model, and the one
/// box you most wanted to click was the one box that was not clickable. Here
/// there is no privileged focus: every box and every line is a target, and
/// hovering either dims everything it does not touch, which is what answers
/// "if I change this, what else moves".
///
/// The flow overlay is the reason the diagram is worth having rather than
/// merely looking at. A flow's *observed* footprint sits beside its
/// *declared* one on the same boxes, so a read nobody declared and a
/// declaration nothing has exercised are both visible where the model is.
class OntologyDiagram extends StatefulWidget {
  final ApiClient api;

  /// Zoom in: show what actually exists of this type. The model and its
  /// instances are one view at two levels, so this is a step down rather than
  /// a second destination — there is no lens chip to arrive at and nothing to
  /// browse until you have said which type you mean.
  final ValueChanged<String> onBrowseInstances;

  /// Open the diagram's own export in the Library. This canvas is a rendering
  /// of the `enterprise-ontology` asset, and for a long time nothing linked
  /// the two — the one governed artifact the whole tab is *about* was
  /// reachable only through the palette.
  final VoidCallback? onOpenExport;

  const OntologyDiagram({
    super.key,
    required this.api,
    required this.onBrowseInstances,
    this.onOpenExport,
  });

  @override
  State<OntologyDiagram> createState() => _OntologyDiagramState();
}

class _OntologyDiagramState extends State<OntologyDiagram> {
  /// Below this the detail panel goes *under* the canvas rather than beside
  /// it. Measured against the pane: 320 of panel and its gutter leave a
  /// canvas worth looking at only above about here.
  static const _sidePanelBreakpoint = 920.0;

  final _view = TransformationController();

  OntologyScreen? _screen;
  OntologyDiagramLayout _layout = OntologyDiagramLayout.empty;
  String? _flow;
  bool _loading = true;
  String? _error;

  String? _selectedType;
  String? _selectedLink;
  String? _hoverType;
  String? _hoverLink;
  Set<String> _expanded = const {};

  /// Read off the transform so the edge labels can drop out when they would
  /// only be noise, and so a hit-test tolerance measured in scene units still
  /// feels the same size under the cursor at every zoom.
  double _scale = 1;
  bool _fitted = false;
  Size _viewport = Size.zero;

  @override
  void initState() {
    super.initState();
    _view.addListener(_onTransform);
    _load();
  }

  @override
  void dispose() {
    _view.removeListener(_onTransform);
    _view.dispose();
    super.dispose();
  }

  void _onTransform() {
    final scale = _view.value.getMaxScaleOnAxis();
    if ((scale - _scale).abs() > 0.005) setState(() => _scale = scale);
  }

  /// Read the model, keeping whatever is already drawn while the read is in
  /// flight. Changing the overlay is a re-read of the same thirty boxes in
  /// the same places, and blanking the canvas to a spinner for it loses the
  /// reader's place for no information — so the previous screen stays up,
  /// the toolbar says a read is running, and the new one swaps in when it
  /// lands. A failure over a screen that is already drawn is a strip above
  /// the canvas rather than a page that eats it.
  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final screen = await widget.api.ontologyScreen(flow: _flow);
      if (!mounted) return;
      setState(() {
        _screen = screen;
        _layout = layoutOntology(screen, expanded: _expanded);
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  void _relayout() {
    final screen = _screen;
    if (screen == null) return;
    _layout = layoutOntology(screen, expanded: _expanded);
  }

  /// Whether the link names are on. Gated against the scale this pane's own
  /// "Fit" lands on, so the opening view of the model always carries them
  /// however far the model grows.
  bool get _showLabels => ontologyLabelsVisible(
    _scale,
    ontologyFitScale(_viewport, _layout.size),
  );

  void _fit() {
    if (_viewport.isEmpty || _layout.size.isEmpty) return;
    final scale = ontologyFitScale(_viewport, _layout.size);
    _view.value = Matrix4.identity()
      ..translateByDouble(
        (_viewport.width - _layout.size.width * scale) / 2,
        (_viewport.height - _layout.size.height * scale) / 2,
        0,
        1,
      )
      ..scaleByDouble(scale, scale, 1, 1);
  }

  void _actualSize() {
    _view.value = Matrix4.identity()..translateByDouble(24, 24, 0, 1);
  }

  /// Nothing under the cursor is a real answer: it clears the selection, so
  /// the canvas is never stuck lit up.
  void _tap(Offset scene) {
    final edge = _layout.edgeAt(scene, tolerance: 12 / _scale);
    setState(() {
      _selectedLink = edge?.link.name;
      _selectedType = null;
    });
  }

  /// Every pointer move over the canvas lands here, so it repaints only when
  /// the answer actually changed: an unguarded `setState` per move rebuilt
  /// thirty node cards and two painters at the frame rate of the mouse.
  void _hover(Offset scene) {
    final name = _layout.edgeAt(scene, tolerance: 10 / _scale)?.link.name;
    if (name != _hoverLink) setState(() => _hoverLink = name);
  }

  /// Exit is reported per box, and moving from one to the next delivers the
  /// leave and the enter in either order — so a box only ever clears the
  /// highlight it is itself holding.
  void _hoverNode(String name, bool on) {
    final next = on ? name : (_hoverType == name ? null : _hoverType);
    if (next != _hoverType) setState(() => _hoverType = next);
  }

  void _leaveCanvas() {
    if (_hoverLink == null && _hoverType == null) return;
    setState(() {
      _hoverLink = null;
      // The node cards' own exit handlers do not fire when the pointer leaves
      // the canvas past the edge of one, which left the diagram lit up around
      // a box the cursor was no longer on.
      _hoverType = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final screen = _screen;
    if (screen == null) {
      // Nothing has ever landed. The toolbar is chrome and renders now; only
      // the canvas is a shape, so the section does not change size when the
      // model arrives.
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _toolbar(null),
          const SizedBox(height: 12),
          if (_loading)
            Expanded(
              child: LayoutBuilder(
                builder: (context, constraints) => SkeletonPulse(
                  child: SkeletonCanvas(height: constraints.maxHeight),
                ),
              ),
            )
          else
            // Nothing is coming, so nothing pulses.
            ErrorNotice(
              message: _error ?? 'The ontology did not answer.',
              onRetry: _load,
            ),
        ],
      );
    }
    final overlay = screen.overlay;
    final detail = _DetailPanel(
      screen: screen,
      selectedType: _selectedType,
      selectedLink: _selectedLink,
      onBrowseInstances: widget.onBrowseInstances,
      onSelectType: (t) => setState(() {
        _selectedType = t;
        _selectedLink = null;
      }),
      onSelectLink: (l) => setState(() {
        _selectedLink = l;
        _selectedType = null;
      }),
      onDismiss: () => setState(() {
        _selectedType = null;
        _selectedLink = null;
      }),
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _toolbar(screen),
        if (_error != null) ...[
          const SizedBox(height: 10),
          // The screen below is the previous one, and saying so is the whole
          // point: a stale model with a strip over it beats a blank page.
          ErrorNotice(
            message: '$_error — showing the last model that answered.',
            onRetry: _load,
          ),
        ],
        if (overlay != null) ...[
          const SizedBox(height: 10),
          _OverlayBanner(overlay: overlay),
        ],
        const SizedBox(height: 12),
        Expanded(
          // The *pane*, not the window. Gating on window width meant a
          // narrow one dimmed the canvas around a selection and then showed
          // nothing of it — the fields, the natural key and "browse the
          // instances" were all unreachable — and subscribing to the window
          // metrics rebuilt the whole canvas on every resize tick besides.
          child: LayoutBuilder(
            builder: (context, constraints) {
              if (constraints.maxWidth > _sidePanelBreakpoint) {
                return Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Expanded(child: _canvas(overlay)),
                    const SizedBox(width: 16),
                    SizedBox(width: 320, child: detail),
                  ],
                );
              }
              // A sheet resting on the canvas rather than a row below it: the
              // canvas keeps its size, so selecting a box does not resize the
              // viewport out from under the transform and re-fit the diagram
              // you had just panned to.
              final selected = _selectedType != null || _selectedLink != null;
              return Stack(
                children: [
                  Positioned.fill(child: _canvas(overlay)),
                  if (selected)
                    Positioned(
                      left: 0,
                      right: 0,
                      bottom: 0,
                      child: ConstrainedBox(
                        constraints: BoxConstraints(
                          maxHeight: math.min(
                            320,
                            constraints.maxHeight * 0.6,
                          ),
                        ),
                        child: detail,
                      ),
                    ),
                ],
              );
            },
          ),
        ),
      ],
    );
  }

  /// The toolbar is chrome, so it draws with or without a model: [screen] is
  /// null only before the first read has landed, and then the controls are
  /// present but inert and the counts are simply absent rather than zero.
  Widget _toolbar(OntologyScreen? screen) {
    final flows = screen?.flows ?? const <OntologyFlowOption>[];
    return Wrap(
      spacing: 10,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        Text('Overlay a flow', style: CumulusText.eyebrow),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          decoration: BoxDecoration(
            color: CumulusColors.surface,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            border: Border.all(color: CumulusColors.borderStrong),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String?>(
              value: _flow,
              hint: const Text('The model alone', style: CumulusText.body),
              style: CumulusText.body,
              borderRadius: BorderRadius.circular(CumulusRadius.control),
              items: [
                const DropdownMenuItem<String?>(
                  value: null,
                  child: Text('The model alone'),
                ),
                for (final flow in flows)
                  DropdownMenuItem<String?>(
                    value: flow.id,
                    child: Text(flow.label),
                  ),
              ],
              onChanged: screen == null
                  ? null
                  : (v) {
                      // A selection made against the previous overlay would
                      // leave the new one dimmed everywhere but one edge,
                      // which hides the very thing the flow was picked to
                      // show.
                      setState(() {
                        _flow = v;
                        _selectedType = null;
                        _selectedLink = null;
                        _hoverType = null;
                        _hoverLink = null;
                      });
                      _load();
                    },
            ),
          ),
        ),
        OutlinedButton(
          onPressed: screen == null ? null : _fit,
          child: const Text('Fit'),
        ),
        OutlinedButton(
          onPressed: screen == null ? null : _actualSize,
          child: const Text('1:1'),
        ),
        // A read over a screen that is already drawn says so here rather than
        // by taking the screen away.
        if (_loading && screen != null)
          const Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              SizedBox(
                width: 13,
                height: 13,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
              SizedBox(width: 8),
              Text('re-reading the model…', style: CumulusText.meta),
            ],
          ),
        if (screen != null) ...[
          MetaPill('${screen.types.length} types'),
          MetaPill('${screen.links.length} links'),
          if (screen.conformance != null)
            MetaPill(
              screen.conformance == 0
                  ? 'conforms'
                  : '${screen.conformance} violation(s)',
              tone: screen.conformance == 0
                  ? CumulusTone.success
                  : CumulusTone.danger,
            ),
        ],
        if (widget.onOpenExport != null)
          Tooltip(
            message:
                'This diagram renders the enterprise-ontology asset — the '
                'declarative export in the Library that the map actually '
                'carries. Open it to read what a customer would keep.',
            child: OutlinedButton.icon(
              onPressed: widget.onOpenExport,
              icon: const Icon(Icons.description_outlined, size: 15),
              label: const Text('Open the export'),
            ),
          ),
      ],
    );
  }

  Widget _canvas(OntologyOverlay? overlay) {
    final activeType = _hoverType ?? _selectedType;
    final activeLink = _hoverLink ?? _selectedLink;
    final focused = activeType != null || activeLink != null;
    final showLabels = _showLabels;
    // Both ends of the lit line, found once. Asking each box in turn whether
    // it touched the active edge walked every edge per box — a thousand-odd
    // comparisons per rebuild, and a rebuild happens on every hover.
    final litEnds = <String>{};
    if (activeLink != null) {
      for (final edge in _layout.edges) {
        if (edge.link.name == activeLink) {
          litEnds
            ..add(edge.link.fromType)
            ..add(edge.link.toType);
        }
      }
    }

    return LayoutBuilder(
      builder: (context, constraints) {
        final viewport = Size(constraints.maxWidth, constraints.maxHeight);
        if (viewport != _viewport) {
          _viewport = viewport;
          _fitted = false;
        }
        if (!_fitted && !viewport.isEmpty) {
          _fitted = true;
          WidgetsBinding.instance.addPostFrameCallback((_) => _fit());
        }
        return DecoratedBox(
          decoration: BoxDecoration(
            color: CumulusColors.surface,
            borderRadius: BorderRadius.circular(CumulusRadius.card),
            border: Border.all(color: CumulusColors.border),
          ),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(CumulusRadius.card),
            child: InteractiveViewer(
              transformationController: _view,
              minScale: ontologyMinScale,
              maxScale: ontologyMaxScale,
              constrained: false,
              boundaryMargin: const EdgeInsets.all(double.infinity),
              child: SizedBox(
                width: _layout.size.width,
                height: _layout.size.height,
                // Both listeners sit *inside* the transformed child, so every
                // pointer position they see is already in scene coordinates.
                // Hit-testing an edge in screen space is the bug that makes a
                // line feel unclickable, and it is invisible until you zoom.
                child: MouseRegion(
                  onHover: (e) => _hover(e.localPosition),
                  onExit: (_) => _leaveCanvas(),
                  child: GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTapUp: (d) => _tap(d.localPosition),
                    child: Stack(
                      clipBehavior: Clip.none,
                      children: [
                        CustomPaint(
                          size: _layout.size,
                          painter: _DiagramPainter(
                            layout: _layout,
                            overlay: overlay,
                            activeType: activeType,
                            activeLink: activeLink,
                            focused: focused,
                            showLabels: showLabels,
                          ),
                        ),
                        for (final node in _layout.nodes)
                          Positioned.fromRect(
                            rect: node.rect,
                            child: _NodeCard(
                              node: node,
                              overlay: overlay,
                              selected: node.name == _selectedType,
                              dimmed:
                                  focused &&
                                  node.name != activeType &&
                                  !litEnds.contains(node.name),
                              onTap: () => setState(() {
                                _selectedType = node.name;
                                _selectedLink = null;
                              }),
                              onHover: (on) => _hoverNode(node.name, on),
                            ),
                          ),
                        // The names ride *above* the boxes: a label the node
                        // covers is worse than no label, and the one you
                        // hovered for is the one you most need to read.
                        IgnorePointer(
                          child: CustomPaint(
                            size: _layout.size,
                            painter: _DiagramPainter(
                              layout: _layout,
                              overlay: overlay,
                              activeType: activeType,
                              activeLink: activeLink,
                              focused: focused,
                              showLabels: showLabels,
                              labelLayer: true,
                            ),
                          ),
                        ),
                        for (final stub in _layout.stubs)
                          Positioned(
                            left: stub.at.dx,
                            top: stub.at.dy - 11,
                            child: Material(
                              color: CumulusColors.surfaceMuted,
                              borderRadius: BorderRadius.circular(
                                CumulusRadius.pill,
                              ),
                              child: InkWell(
                                onTap: () => setState(() {
                                  _expanded = {..._expanded, stub.node.name};
                                  _relayout();
                                }),
                                borderRadius: BorderRadius.circular(
                                  CumulusRadius.pill,
                                ),
                                child: Padding(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 8,
                                    vertical: 4,
                                  ),
                                  child: Text(
                                    '+${stub.hidden.length} more',
                                    style: CumulusText.statCaption,
                                  ),
                                ),
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        );
      },
    );
  }

}

// ---------------------------------------------------------------------------
// The flow overlay
// ---------------------------------------------------------------------------

/// What the selected flow actually did, and where that differs from what it
/// declared. The diff is the finding, so it is stated in words here as well
/// as drawn on the boxes — a tinted chip is a prompt to look, not a report.
class _OverlayBanner extends StatelessWidget {
  final OntologyOverlay overlay;

  const _OverlayBanner({required this.overlay});

  @override
  Widget build(BuildContext context) {
    final gaps = overlay.gaps;
    final stale = <String>[
      for (final t in {
        ...overlay.declaredReads,
        ...overlay.declaredWrites,
      }.toList()..sort())
        if (overlay.read(t) == Footprint.stale ||
            overlay.write(t) == Footprint.stale)
          t,
    ];
    final observation = switch (overlay.state) {
      'never_ran' =>
        'Never ran, so nothing is observed. What it declares is drawn in '
            'outline, because an untested declaration is still a claim.',
      'ran_but_wrote_nothing' =>
        'Ran ${overlay.totalRuns} time(s) and wrote nothing to the graph — '
            'a different fact from never having run.',
      _ =>
        'Observed across ${overlay.scannedRuns} of ${overlay.totalRuns} '
            'run(s).',
    };

    return CumulusCard(
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
      borderColor: gaps.isEmpty ? null : CumulusColors.danger,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(overlay.flowId, style: CumulusText.rowTitle),
              StatusBadge(
                overlay.state.replaceAll('_', ' '),
                tone: overlay.everRan ? CumulusTone.accent : CumulusTone.warn,
                dense: true,
              ),
              if (gaps.isNotEmpty)
                StatusBadge(
                  '${gaps.length} governance gap(s)',
                  tone: CumulusTone.danger,
                  dense: true,
                ),
              if (stale.isNotEmpty)
                StatusBadge(
                  '${stale.length} stale declaration(s)',
                  tone: CumulusTone.warn,
                  dense: true,
                ),
            ],
          ),
          const SizedBox(height: 6),
          Text(observation, style: CumulusText.meta),
          if (gaps.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                'Touched but never declared: ${gaps.join(', ')}.',
                style: CumulusText.meta.copyWith(color: CumulusColors.danger),
              ),
            ),
          if (stale.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(
                'Declared but never observed: ${stale.join(', ')}.',
                style: CumulusText.meta.copyWith(color: CumulusColors.warn),
              ),
            ),
        ],
      ),
    );
  }
}

/// One read or write mark. Solid means observed; an outline means declared
/// and not seen; danger means observed and not declared.
Widget _mark(String letter, Footprint footprint) {
  final tone = switch (footprint) {
    Footprint.undeclared => CumulusTone.danger,
    Footprint.matched => CumulusTone.accent,
    _ => CumulusTone.neutral,
  };
  final outline = footprint == Footprint.stale;
  return Container(
    width: 15,
    height: 15,
    alignment: Alignment.center,
    decoration: BoxDecoration(
      color: outline ? Colors.transparent : tone.background,
      borderRadius: BorderRadius.circular(4),
      border: Border.all(
        color: outline ? CumulusColors.borderStrong : Colors.transparent,
      ),
    ),
    child: Text(
      letter,
      style: CumulusText.statLabel.copyWith(
        fontSize: 9.5,
        color: outline ? CumulusColors.inkMuted : tone.foreground,
      ),
    ),
  );
}

// ---------------------------------------------------------------------------
// Nodes
// ---------------------------------------------------------------------------

class _NodeCard extends StatelessWidget {
  final OntologyNode node;
  final OntologyOverlay? overlay;
  final bool selected;
  final bool dimmed;
  final VoidCallback onTap;
  final ValueChanged<bool> onHover;

  const _NodeCard({
    required this.node,
    required this.overlay,
    required this.selected,
    required this.dimmed,
    required this.onTap,
    required this.onHover,
  });

  @override
  Widget build(BuildContext context) {
    final read = overlay?.read(node.name) ?? Footprint.absent;
    final write = overlay?.write(node.name) ?? Footprint.absent;
    final gap = read == Footprint.undeclared || write == Footprint.undeclared;
    final touched = read != Footprint.absent || write != Footprint.absent;

    final border = gap
        ? CumulusColors.danger
        : selected
        ? CumulusColors.accent
        : touched
        ? CumulusColors.accentSoft
        : CumulusColors.borderStrong;

    return Opacity(
      opacity: dimmed ? 0.28 : 1,
      child: MouseRegion(
        onEnter: (_) => onHover(true),
        onExit: (_) => onHover(false),
        child: Material(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          child: InkWell(
            onTap: onTap,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            hoverColor: CumulusColors.surfaceMuted,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(CumulusRadius.control),
                border: Border.all(
                  color: border,
                  width: selected || gap ? 1.5 : 1,
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          node.type.label,
                          overflow: TextOverflow.ellipsis,
                          style: CumulusText.rowTitle.copyWith(fontSize: 12.5),
                        ),
                      ),
                      if (read != Footprint.absent) _mark('R', read),
                      if (write != Footprint.absent) ...[
                        const SizedBox(width: 3),
                        _mark('W', write),
                      ],
                    ],
                  ),
                  Text(
                    node.type.isPart
                        ? 'part · ${node.type.instances}'
                        : '${node.type.instances} instance(s)',
                    overflow: TextOverflow.ellipsis,
                    style: CumulusText.statCaption.copyWith(fontSize: 10.5),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Edges, cluster frames and self-loop glyphs
// ---------------------------------------------------------------------------

class _DiagramPainter extends CustomPainter {
  final OntologyDiagramLayout layout;
  final OntologyOverlay? overlay;
  final String? activeType;
  final String? activeLink;
  final bool focused;
  final bool showLabels;

  /// The second pass, drawn over the node widgets: link names only.
  final bool labelLayer;

  _DiagramPainter({
    required this.layout,
    required this.overlay,
    required this.activeType,
    required this.activeLink,
    required this.focused,
    required this.showLabels,
    this.labelLayer = false,
  });

  static Color _natureColor(String nature) => switch (nature) {
    'composition' => CumulusColors.accent,
    'identity' => CumulusColors.teal,
    _ => CumulusColors.inkMuted,
  };

  @override
  void paint(Canvas canvas, Size size) {
    if (!labelLayer) {
      for (final box in layout.clusters) {
        _cluster(canvas, box);
      }
      for (final edge in layout.edges) {
        _edge(canvas, edge);
      }
      for (final loop in layout.selfLoops) {
        _selfLoop(canvas, loop);
      }
      return;
    }
    // Names claim their space first-come, and a name with nowhere left to sit
    // waits for a hover rather than printing over the one already there. The
    // highlighted ones go last so they can always take the spot they want:
    // hovering a line is how you ask for exactly that label.
    final claimed = <Rect>[];
    for (final edge in layout.edges) {
      if (_isActive(edge)) _label(canvas, edge, true, false, claimed);
    }
    for (final loop in layout.selfLoops) {
      _selfLoop(canvas, loop, claimed);
    }
    for (final edge in layout.edges) {
      if (_isActive(edge) || !(showLabels && edge.labelFits)) continue;
      _label(canvas, edge, false, focused, claimed);
    }
  }

  bool _isActive(OntologyEdge edge) =>
      edge.link.name == activeLink ||
      (activeType != null &&
          (edge.link.fromType == activeType || edge.link.toType == activeType));

  void _cluster(Canvas canvas, OntologyClusterBox box) {
    final rrect = RRect.fromRectAndRadius(
      box.rect,
      const Radius.circular(CumulusRadius.card),
    );
    canvas.drawRRect(
      rrect,
      Paint()..color = CumulusColors.canvas.withValues(alpha: 0.75),
    );
    canvas.drawRRect(
      rrect,
      Paint()
        ..style = PaintingStyle.stroke
        ..color = box.cluster.id == ungovernedCluster
            ? CumulusColors.danger
            : CumulusColors.border
        ..strokeWidth = 1,
    );
    _text(
      canvas,
      box.cluster.label.toUpperCase(),
      Offset(box.rect.left + 16, box.rect.top + 10),
      CumulusText.statLabel.copyWith(fontSize: 9.5),
    );
    _text(
      canvas,
      box.cluster.origin,
      Offset(box.rect.right - 16, box.rect.top + 10),
      CumulusText.statCaption.copyWith(fontSize: 9.5),
      alignRight: true,
    );
  }

  void _edge(Canvas canvas, OntologyEdge edge) {
    final highlighted = _isActive(edge);
    final establishes = overlay?.establishes.contains(edge.link.name) ?? false;
    final dim = focused && !highlighted;

    var color = highlighted
        ? CumulusColors.accentInk
        : establishes
        ? CumulusColors.accent
        : _natureColor(edge.link.nature);
    color = color.withValues(
      alpha: dim
          ? 0.16
          : highlighted || establishes
          ? 0.95
          : 0.55,
    );

    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = highlighted
          ? 2.2
          : edge.link.nature == 'composition' || establishes
          ? 1.8
          : 1.2
      ..color = color;

    final path = Path()
      ..moveTo(edge.start.dx, edge.start.dy)
      ..quadraticBezierTo(
        edge.control.dx,
        edge.control.dy,
        edge.end.dx,
        edge.end.dy,
      );
    // An identity assertion is not a fact about the world the way a
    // composition is, and the dash says so without a legend.
    canvas.drawPath(
      edge.link.nature == 'identity' ? _dashed(path) : path,
      paint,
    );

    // A back-edge keeps its true direction and takes a hollow head instead:
    // reversing it to make the layout tidy would make the picture wrong.
    _arrow(canvas, edge, color, hollow: edge.backEdge);
  }

  void _arrow(
    Canvas canvas,
    OntologyEdge edge,
    Color color, {
    required bool hollow,
  }) {
    final approach = edge.pointAt(0.92);
    final angle = math.atan2(
      edge.end.dy - approach.dy,
      edge.end.dx - approach.dx,
    );
    const len = 9.0;
    const spread = 0.42;
    final path = Path()
      ..moveTo(edge.end.dx, edge.end.dy)
      ..lineTo(
        edge.end.dx - len * math.cos(angle - spread),
        edge.end.dy - len * math.sin(angle - spread),
      )
      ..lineTo(
        edge.end.dx - len * math.cos(angle + spread),
        edge.end.dy - len * math.sin(angle + spread),
      )
      ..close();
    canvas.drawPath(
      path,
      Paint()
        ..color = hollow ? CumulusColors.surface : color
        ..style = PaintingStyle.fill,
    );
    if (hollow) {
      canvas.drawPath(
        path,
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2,
      );
    }
  }

  void _label(
    Canvas canvas,
    OntologyEdge edge,
    bool highlighted,
    bool dim,
    List<Rect> claimed,
  ) {
    final style = CumulusText.mono.copyWith(
      fontSize: 10,
      color:
          (highlighted ? CumulusColors.accentInk : CumulusColors.inkSecondary)
              .withValues(alpha: dim ? 0.25 : 1),
    );
    final painter = TextPainter(
      text: TextSpan(text: edge.link.name, style: style),
      textDirection: TextDirection.ltr,
    )..layout();
    // The midpoint is only the first choice. A name that would land on one
    // already drawn slides along its own curve before it gives up, which
    // keeps far more of them on screen than a plain drop rule does.
    Rect? rect;
    for (final t in const [0.5, 0.36, 0.64, 0.26, 0.74]) {
      final candidate = Rect.fromCenter(
        center: edge.pointAt(t),
        width: painter.width + 10,
        height: painter.height + 5,
      );
      if (!claimed.any(candidate.overlaps)) {
        rect = candidate;
        break;
      }
    }
    if (rect == null) return;
    claimed.add(rect);
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(5)),
      Paint()
        ..color = CumulusColors.surface.withValues(alpha: dim ? 0.5 : 0.94),
    );
    if (highlighted) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(rect, const Radius.circular(5)),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1
          ..color = CumulusColors.accent,
      );
    }
    painter.paint(canvas, rect.topLeft + const Offset(5, 2.5));
  }

  /// A hooked arc on the box's own corner. A self-link drawn as a line has to
  /// leave a box and come back to it, which is the ugliest shape in any
  /// schema diagram and says nothing this does not.
  void _selfLoop(Canvas canvas, OntologySelfLoop loop, [List<Rect>? claimed]) {
    final highlighted =
        loop.link.name == activeLink || loop.node.name == activeType;
    final color =
        (highlighted ? CumulusColors.accentInk : CumulusColors.inkMuted)
            .withValues(alpha: focused && !highlighted ? 0.2 : 0.8);
    final centre = loop.at + const Offset(0, -7);
    if (labelLayer) {
      if (!showLabels && !highlighted) return;
      final at = centre + const Offset(12, -6);
      final rect = Rect.fromLTWH(at.dx, at.dy, 6.4 * loop.link.name.length, 13);
      if (!highlighted && (claimed?.any(rect.overlaps) ?? false)) return;
      claimed?.add(rect);
      _text(
        canvas,
        loop.link.name,
        at,
        CumulusText.mono.copyWith(fontSize: 9.5, color: color),
      );
      return;
    }
    canvas.drawArc(
      Rect.fromCircle(center: centre, radius: 8),
      -math.pi * 0.75,
      math.pi * 1.5,
      false,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.4
        ..color = color,
    );
    canvas.drawCircle(
      centre + const Offset(5.7, 5.7),
      2,
      Paint()..color = color,
    );
  }

  void _text(
    Canvas canvas,
    String value,
    Offset at,
    TextStyle style, {
    bool alignRight = false,
  }) {
    final painter = TextPainter(
      text: TextSpan(text: value, style: style),
      textDirection: TextDirection.ltr,
    )..layout();
    painter.paint(canvas, alignRight ? at - Offset(painter.width, 0) : at);
  }

  Path _dashed(Path source) {
    final dashed = Path();
    for (final metric in source.computeMetrics()) {
      var at = 0.0;
      while (at < metric.length) {
        final end = math.min(at + 5, metric.length);
        dashed.addPath(metric.extractPath(at, end), Offset.zero);
        at = end + 4;
      }
    }
    return dashed;
  }

  /// A hover rebuilds this subtree, so the cheap answer matters: the layout
  /// and the overlay are whole objects replaced only when a read lands or a
  /// stub is expanded, and comparing them by identity is both correct and
  /// O(1) — a field-by-field `==` over forty-five edges would cost more per
  /// pointer move than the repaint it is trying to avoid.
  @override
  bool shouldRepaint(covariant _DiagramPainter old) =>
      !identical(old.layout, layout) ||
      !identical(old.overlay, overlay) ||
      old.activeType != activeType ||
      old.activeLink != activeLink ||
      old.focused != focused ||
      old.showLabels != showLabels ||
      old.labelLayer != labelLayer;
}

// ---------------------------------------------------------------------------
// What one box or one line actually says
// ---------------------------------------------------------------------------

class _DetailPanel extends StatelessWidget {
  final OntologyScreen screen;
  final String? selectedType;
  final String? selectedLink;
  final ValueChanged<String> onSelectType;
  final ValueChanged<String> onSelectLink;
  final ValueChanged<String> onBrowseInstances;

  /// Clears the selection. Offered where the panel is stacked under the
  /// canvas and is taking room the diagram wants back; the side panel has
  /// nothing to give up, so it does not show it.
  final VoidCallback? onDismiss;

  const _DetailPanel({
    required this.screen,
    required this.selectedType,
    required this.selectedLink,
    required this.onSelectType,
    required this.onSelectLink,
    required this.onBrowseInstances,
    this.onDismiss,
  });

  @override
  Widget build(BuildContext context) {
    OntologyType? type;
    for (final candidate in screen.types) {
      if (candidate.name == selectedType) type = candidate;
    }
    OntologyLink? link;
    for (final candidate in screen.links) {
      if (candidate.name == selectedLink) link = candidate;
    }

    return CumulusCard(
      padding: const EdgeInsets.fromLTRB(18, 18, 18, 18),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (onDismiss != null && (type != null || link != null))
              Align(
                alignment: Alignment.topRight,
                child: InkWell(
                  onTap: onDismiss,
                  borderRadius: BorderRadius.circular(CumulusRadius.pill),
                  child: const Padding(
                    padding: EdgeInsets.all(4),
                    child: Icon(
                      Icons.close,
                      size: 15,
                      color: CumulusColors.inkMuted,
                    ),
                  ),
                ),
              ),
            if (type != null)
              ..._type(type)
            else if (link != null)
              ..._link(link)
            else ...[
              Text('THE MODEL', style: CumulusText.eyebrow),
              const SizedBox(height: 8),
              const Text(
                'Every box and every line is clickable. Hover one to see '
                'what it touches; pick a flow above to see what a flow '
                'actually reads and writes against what it declared. Pick a '
                'box to read its fields, and to browse what exists of it.',
                style: CumulusText.body,
              ),
            ],
          ],
        ),
      ),
    );
  }

  List<Widget> _type(OntologyType type) {
    final incoming = screen.links.where((l) => l.toType == type.name).toList();
    final outgoing = screen.links
        .where((l) => l.fromType == type.name)
        .toList();
    return [
      Text('OBJECT TYPE', style: CumulusText.eyebrow),
      const SizedBox(height: 6),
      Text(type.label, style: CumulusText.pageTitle.copyWith(fontSize: 20)),
      const SizedBox(height: 8),
      Wrap(
        spacing: 6,
        runSpacing: 6,
        children: [
          MetaPill('${type.instances} instance(s)'),
          MetaPill(type.collection),
          if (type.conflicted > 0)
            MetaPill('${type.conflicted} conflicted', tone: CumulusTone.warn),
          if (type.isPart)
            MetaPill('part of ${type.ownerType}', tone: CumulusTone.accent),
        ],
      ),
      if (type.summary.isNotEmpty) ...[
        const SizedBox(height: 10),
        Text(type.summary, style: CumulusText.body),
      ],
      const SizedBox(height: 12),
      // The declared type and what exists of it are the same subject at two
      // zoom levels. A type nothing has instantiated is a promise nobody has
      // kept, and offering a browse that lands on an empty list would say
      // that less clearly than refusing to offer it.
      SizedBox(
        width: double.infinity,
        child: type.instances == 0
            ? const Text(
                'Nothing exists of this type yet.',
                style: CumulusText.meta,
              )
            : OutlinedButton.icon(
                onPressed: () => onBrowseInstances(type.name),
                icon: const Icon(Icons.arrow_downward, size: 15),
                label: Text('Browse ${type.instances} instance(s)'),
              ),
      ),
      if (type.naturalKey.isNotEmpty) ...[
        const SizedBox(height: 10),
        Text('IDENTIFIED BY', style: CumulusText.eyebrow),
        const SizedBox(height: 4),
        Text(type.naturalKey, style: CumulusText.meta),
      ],
      if (type.fields.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text('FIELDS', style: CumulusText.eyebrow),
        const SizedBox(height: 4),
        for (final field in type.fields)
          Text(
            '${field.name} · ${field.kind}',
            style: CumulusText.mono.copyWith(fontSize: 11),
          ),
      ],
      if (outgoing.isNotEmpty || incoming.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text('LINKS', style: CumulusText.eyebrow),
        for (final link in outgoing) _linkRow(link, '→ ${link.toType}'),
        for (final link in incoming) _linkRow(link, '← ${link.fromType}'),
      ],
      if (type.competencyQuestions.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text('ANSWERS', style: CumulusText.eyebrow),
        const SizedBox(height: 4),
        for (final question in type.competencyQuestions)
          Text('· $question', style: CumulusText.meta),
      ],
    ];
  }

  Widget _linkRow(OntologyLink link, String other) => InkWell(
    onTap: () => onSelectLink(link.name),
    child: Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Text(
        '${link.name} $other',
        style: CumulusText.mono.copyWith(
          fontSize: 11,
          color: _DiagramPainter._natureColor(link.nature),
        ),
        overflow: TextOverflow.ellipsis,
      ),
    ),
  );

  List<Widget> _link(OntologyLink link) => [
    Text('LINK TYPE', style: CumulusText.eyebrow),
    const SizedBox(height: 6),
    Text(link.name, style: CumulusText.pageTitle.copyWith(fontSize: 19)),
    const SizedBox(height: 10),
    Row(
      children: [
        Flexible(
          child: InkWell(
            onTap: () => onSelectType(link.fromType),
            child: Text(link.fromType, style: CumulusText.rowTitle),
          ),
        ),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 8),
          child: Icon(
            Icons.arrow_forward,
            size: 14,
            color: CumulusColors.inkMuted,
          ),
        ),
        Flexible(
          child: InkWell(
            onTap: () => onSelectType(link.toType),
            child: Text(link.toType, style: CumulusText.rowTitle),
          ),
        ),
      ],
    ),
    const SizedBox(height: 12),
    Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        MetaPill(
          link.nature,
          tone: switch (link.nature) {
            'composition' => CumulusTone.accent,
            'identity' => CumulusTone.teal,
            _ => null,
          },
        ),
        MetaPill(link.cardinality),
        MetaPill(
          link.enforced ? 'enforced' : 'not enforced',
          tone: link.enforced ? CumulusTone.success : CumulusTone.neutral,
        ),
        MetaPill('${link.instances} edge(s)'),
      ],
    ),
    if (!link.enforced) ...[
      const SizedBox(height: 10),
      const Text(
        'Unenforced: a second edge here is legitimate, so this is a decision '
        'rather than an omission.',
        style: CumulusText.meta,
      ),
    ],
  ];
}
