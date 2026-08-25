import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/ui_kit.dart';
import 'api_docs_data.dart';

/// The API reference, reachable from the help menu in the top bar.
///
/// Pushed as a full route rather than folded into the rail: it is a reference
/// you open, read, and leave, not one of the console's working surfaces.
class ApiDocsScreen extends StatefulWidget {
  const ApiDocsScreen({super.key});

  @override
  State<ApiDocsScreen> createState() => _ApiDocsScreenState();
}

class _ApiDocsScreenState extends State<ApiDocsScreen> {
  static const _tabs = ['Getting started', 'REST API', 'MCP', 'A2A'];
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CumulusColors.canvas,
      body: SafeArea(
        child: PageBody(
          padding: const EdgeInsets.fromLTRB(28, 16, 28, 64),
          maxWidth: 1080,
          children: [
            BackLink(
              label: 'Back to console',
              onTap: () => Navigator.pop(context),
            ),
            const PageHeading(
              eyebrow: 'Reference',
              title: 'API documentation',
              subtitle:
                  'Three front doors onto the same knowledge layer, sharing '
                  'one set of credentials and one authorization model: a REST '
                  'admin API, an MCP server for AI clients, and an A2A agent '
                  'endpoint.',
            ),
            const _ConnectionCard(),
            SegmentedTabs(
              labels: _tabs,
              selectedIndex: _tab,
              onChanged: (i) => setState(() => _tab = i),
            ),
            ...switch (_tab) {
              0 => const [_GettingStarted()],
              1 => [for (final g in restGroups) _GroupCard(group: g)],
              2 => const [_McpSection()],
              _ => const [_GroupSection(group: a2aGroup)],
            },
          ],
        ),
      ),
    );
  }
}

// ---- Connection -------------------------------------------------------------

class _ConnectionCard extends StatelessWidget {
  const _ConnectionCard();

  @override
  Widget build(BuildContext context) {
    // The value the running app is actually configured with, so what is on
    // screen is what your requests will hit — not a placeholder to edit.
    final base = apiBaseUrl;
    return SectionCard(
      title: 'Base URL',
      subtitle: 'this console is pointed here',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        spacing: 12,
        children: [
          _CodeBlock(content: base, label: 'Base URL'),
          Text(
            'Set at build time with --dart-define=API_BASE_URL=… . Every path '
            'below is relative to it.',
            style: CumulusText.meta,
          ),
        ],
      ),
    );
  }
}

// ---- Getting started --------------------------------------------------------

class _GettingStarted extends StatelessWidget {
  const _GettingStarted();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 16,
      children: [
        SectionCard(
          title: 'Authentication',
          subtitle: 'three credential types, accepted everywhere',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 14,
            children: [
              Text(
                'All three front doors resolve credentials through the same '
                'code path, so a credential that works against REST works '
                'against MCP and A2A unchanged. A supplied-but-invalid '
                'X-API-Key is always a hard 401 — it never falls through to '
                'the permissive local-development mode.',
                style: CumulusText.body,
              ),
              const _CredentialRow(
                header: 'Authorization: Bearer <token>',
                title: 'Firebase ID token',
                detail:
                    'For people signed into the console. Role comes from the '
                    '`role` custom claim.',
              ),
              const _CredentialRow(
                header: 'X-API-Key: cumk_<id>_<secret>',
                title: 'Minted API key',
                detail:
                    'For agents and integrations. Acts as the identity it was '
                    'minted for and resolves that identity\'s role on every '
                    'request, so revoking authority does not require touching '
                    'the key. Per-client, revocable, and attributed.',
              ),
              const _CredentialRow(
                header: 'X-API-Key: <ADMIN_API_KEY>',
                title: 'Legacy root credential',
                detail:
                    'Owner rights, unrevocable, rotated only by redeploying. '
                    'Keep it for bootstrapping; prefer a minted key for '
                    'anything long-lived.',
                tone: CumulusTone.warn,
              ),
            ],
          ),
        ),
        SectionCard(
          title: 'Authority',
          subtitle: 'two axes',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 12,
            children: [
              Text(
                'A platform role (owner ▸ admin ▸ user) and a per-team role '
                '(admin, member). They are independent: a plain user who '
                'administers the finance team may write finance\'s documents '
                'and nothing else. Endpoints below state which they need.',
                style: CumulusText.body,
              ),
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: CumulusColors.warnSoft,
                  borderRadius: BorderRadius.circular(CumulusRadius.control),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(Icons.key_off_outlined,
                        size: 17, color: CumulusColors.warn),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        'Routes marked "keys refused" reject minted API keys '
                        'even when the key carries admin authority. Managing '
                        'credentials, identities, and team membership is a '
                        'human action — otherwise a key could widen its own '
                        'access.',
                        style: CumulusText.body
                            .copyWith(color: CumulusColors.warn),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SectionCard(
          title: 'Errors',
          subtitle: 'one shape, everywhere',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 12,
            children: [
              _CodeBlock(
                content: '{"detail": "Asset not found: vpn-setup-note"}',
                label: 'Error body',
              ),
              Text(
                'On 422, `detail` is a list of field-level validation errors '
                'rather than a string. Unhandled failures return 500 with a '
                '`request_id` you can grep the logs for. A2A is the one '
                'exception: JSON-RPC protocol errors come back with HTTP 200 '
                'and an `error` object, as that spec requires.',
                style: CumulusText.body,
              ),
            ],
          ),
        ),
        SectionCard(
          title: 'First request',
          subtitle: 'verify a credential, then search',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 12,
            children: [
              Text(
                '/api/v1/users/me echoes back the principal the server '
                'resolved. It is the fastest way to tell a bad credential '
                'from a permissions problem.',
                style: CumulusText.body,
              ),
              _CodeBlock(
                label: 'curl',
                content: 'curl -s "$apiBaseUrl/api/v1/users/me" \\\n'
                    '  -H "X-API-Key: cumk_<id>_<secret>"\n'
                    '\n'
                    'curl -s "$apiBaseUrl/api/v1/search?q=expense+approval&limit=5" \\\n'
                    '  -H "X-API-Key: cumk_<id>_<secret>"',
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _CredentialRow extends StatelessWidget {
  final String header;
  final String title;
  final String detail;
  final CumulusTone tone;

  const _CredentialRow({
    required this.header,
    required this.title,
    required this.detail,
    this.tone = CumulusTone.neutral,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: CumulusColors.surfaceMuted,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(color: CumulusColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(title, style: CumulusText.rowTitle),
              ),
              if (tone != CumulusTone.neutral)
                const StatusBadge('legacy', tone: CumulusTone.warn, dense: true),
            ],
          ),
          const SizedBox(height: 6),
          SelectableText(header, style: CumulusText.mono),
          const SizedBox(height: 8),
          Text(detail, style: CumulusText.meta),
        ],
      ),
    );
  }
}

// ---- Endpoint rendering -----------------------------------------------------

class _GroupCard extends StatelessWidget {
  final EndpointGroup group;

  const _GroupCard({required this.group});

  @override
  Widget build(BuildContext context) => _GroupSection(group: group);
}

class _GroupSection extends StatelessWidget {
  final EndpointGroup group;

  const _GroupSection({required this.group});

  @override
  Widget build(BuildContext context) {
    return CumulusCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SectionHeader(
                  title: group.title,
                  subtitle: '${group.endpoints.length} endpoints',
                ),
                const SizedBox(height: 10),
                Text(group.blurb, style: CumulusText.body),
              ],
            ),
          ),
          for (final endpoint in group.endpoints) ...[
            const Divider(height: 1),
            _EndpointTile(endpoint: endpoint),
          ],
        ],
      ),
    );
  }
}

/// One endpoint, collapsed to its signature until opened. A reference this
/// long is unreadable fully expanded, and the method-plus-path line is what
/// people scan for.
class _EndpointTile extends StatelessWidget {
  final Endpoint endpoint;

  const _EndpointTile({required this.endpoint});

  @override
  Widget build(BuildContext context) {
    return Theme(
      // The default expansion tile paints its own dividers and tint over the
      // card's hairline borders.
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
        childrenPadding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        shape: const Border(),
        collapsedShape: const Border(),
        title: Row(
          children: [
            _MethodChip(endpoint.method),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                endpoint.path,
                style: CumulusText.mono.copyWith(fontSize: 13),
              ),
            ),
            const SizedBox(width: 12),
            _AuthBadge(endpoint.auth),
          ],
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 6, left: 62),
          child: Text(endpoint.summary, style: CumulusText.meta),
        ),
        children: [
          if (endpoint.notes != null) ...[
            Text(endpoint.notes!, style: CumulusText.body),
            const SizedBox(height: 16),
          ],
          if (endpoint.params.isNotEmpty) ...[
            const FieldLabel('Parameters'),
            _ParamTable(params: endpoint.params),
            const SizedBox(height: 16),
          ],
          if (endpoint.requestExample != null) ...[
            _CodeBlock(
              label: 'Request body',
              content: endpoint.requestExample!,
            ),
            const SizedBox(height: 16),
          ],
          _CodeBlock(
            label: 'Response · ${endpoint.status}',
            content: endpoint.responseExample ?? _defaultResponseNote(endpoint),
          ),
          if (endpoint.errors.isNotEmpty) ...[
            const SizedBox(height: 16),
            const FieldLabel('Errors'),
            for (final error in endpoint.errors)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 44,
                      child: StatusBadge(
                        '${error.status}',
                        tone: error.status >= 500
                            ? CumulusTone.danger
                            : error.status >= 400 && error.status < 404
                            ? CumulusTone.warn
                            : CumulusTone.neutral,
                        dense: true,
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(error.detail, style: CumulusText.meta),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }

  static String _defaultResponseNote(Endpoint endpoint) {
    if (endpoint.status == 204) return '204 No Content — empty body.';
    return 'Returns the affected resource.';
  }
}

class _MethodChip extends StatelessWidget {
  final String method;

  const _MethodChip(this.method);

  CumulusTone get _tone => switch (method) {
    'GET' => CumulusTone.teal,
    'POST' => CumulusTone.success,
    'PUT' || 'PATCH' => CumulusTone.warn,
    'DELETE' => CumulusTone.danger,
    _ => CumulusTone.neutral,
  };

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 62,
      child: Container(
        alignment: Alignment.center,
        padding: const EdgeInsets.symmetric(vertical: 5),
        decoration: BoxDecoration(
          color: _tone.background,
          borderRadius: BorderRadius.circular(7),
        ),
        child: Text(
          method,
          style: TextStyle(
            fontSize: 10.5,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.6,
            color: _tone.foreground,
          ),
        ),
      ),
    );
  }
}

class _AuthBadge extends StatelessWidget {
  final ApiAuth auth;

  const _AuthBadge(this.auth);

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (auth.keysRefused) ...[
          const Tooltip(
            message: 'Minted API keys are refused on this route',
            child: Icon(Icons.key_off_outlined,
                size: 14, color: CumulusColors.warn),
          ),
          const SizedBox(width: 6),
        ],
        StatusBadge(
          auth.label,
          tone: auth == ApiAuth.open
              ? CumulusTone.neutral
              : auth == ApiAuth.owner
              ? CumulusTone.danger
              : CumulusTone.accent,
          dense: true,
        ),
      ],
    );
  }
}

class _ParamTable extends StatelessWidget {
  final List<ApiParam> params;

  const _ParamTable({required this.params});

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        for (final param in params)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 170,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SelectableText(
                        param.name,
                        style: CumulusText.mono.copyWith(fontSize: 12),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        param.where,
                        style: CumulusText.sectionSubtitle,
                      ),
                    ],
                  ),
                ),
                SizedBox(
                  width: 120,
                  child: Text(param.type, style: CumulusText.meta),
                ),
                SizedBox(
                  width: 92,
                  child: param.required
                      ? const StatusBadge('required',
                          tone: CumulusTone.accent, dense: true)
                      : Text(
                          param.defaultValue == null
                              ? 'optional'
                              : '= ${param.defaultValue}',
                          style: CumulusText.sectionSubtitle,
                        ),
                ),
                Expanded(
                  child: Text(param.description, style: CumulusText.meta),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

// ---- MCP --------------------------------------------------------------------

class _McpSection extends StatelessWidget {
  const _McpSection();

  @override
  Widget build(BuildContext context) {
    final url = '$apiBaseUrl/mcp/';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 16,
      children: [
        SectionCard(
          title: 'Endpoint',
          subtitle: 'Streamable HTTP',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 12,
            children: [
              _CodeBlock(content: url, label: 'MCP endpoint'),
              Text(
                'Stateless JSON responses, which suits Cloud Run. Credentials '
                'are enforced at the transport, so the handshake and every '
                'tool and resource call are covered by one check — an '
                'unauthenticated client is rejected before it reaches the '
                'protocol handler, as a JSON-RPC error with HTTP 401.',
                style: CumulusText.body,
              ),
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: CumulusColors.warnSoft,
                  borderRadius: BorderRadius.circular(CumulusRadius.control),
                ),
                child: Text(
                  'API keys are the interim mechanism here. The MCP '
                  'authorization spec expects an OAuth 2.1 resource-server '
                  'flow; until that lands, minted keys are at least '
                  'per-client, revocable, and attributable.',
                  style: CumulusText.body.copyWith(color: CumulusColors.warn),
                ),
              ),
            ],
          ),
        ),
        SectionCard(
          title: 'Client configuration',
          subtitle: 'minting a key returns this ready to paste',
          child: _CodeBlock(
            label: 'mcp.json',
            content: '''{
  "servers": {
    "cumulus-knowledge": {
      "type": "http",
      "url": "$url",
      "headers": {"X-API-Key": "cumk_<id>_<secret>"}
    }
  }
}''',
          ),
        ),
        CumulusCard(
          padding: EdgeInsets.zero,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Padding(
                padding: EdgeInsets.fromLTRB(20, 18, 20, 14),
                child: SectionHeader(
                  title: 'Tools',
                  subtitle: 'what an AI client can call',
                ),
              ),
              for (final tool in mcpTools) ...[
                const Divider(height: 1),
                Padding(
                  padding: const EdgeInsets.all(20),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SelectableText(
                        tool.signature,
                        style: CumulusText.mono.copyWith(fontSize: 12.5),
                      ),
                      const SizedBox(height: 8),
                      Text(tool.description, style: CumulusText.body),
                    ],
                  ),
                ),
              ],
              const Divider(height: 1),
              const Padding(
                padding: EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SelectableText('kb://{doc_id}', style: CumulusText.mono),
                    SizedBox(height: 8),
                    Text(
                      'Resource. Document content by id, for clients that '
                      'prefer resources over tool calls.',
                      style: CumulusText.body,
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

// ---- Shared -----------------------------------------------------------------

class _CodeBlock extends StatelessWidget {
  final String content;
  final String label;

  const _CodeBlock({required this.content, required this.label});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            FieldLabel(label),
            const Spacer(),
            _CopyButton(content: content, label: label),
          ],
        ),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: CumulusColors.surfaceMuted,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            border: Border.all(color: CumulusColors.border),
          ),
          // Long request lines must scroll inside the block rather than
          // widening the page.
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: SelectableText(content, style: CumulusText.mono),
          ),
        ),
      ],
    );
  }
}

class _CopyButton extends StatelessWidget {
  final String content;
  final String label;

  const _CopyButton({required this.content, required this.label});

  @override
  Widget build(BuildContext context) {
    return TextButton.icon(
      onPressed: () async {
        await Clipboard.setData(ClipboardData(text: content));
        if (!context.mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('$label copied to clipboard')),
        );
      },
      icon: const Icon(Icons.copy, size: 14),
      label: const Text('Copy'),
    );
  }
}
