import 'package:flutter/material.dart';

import '../widgets/ui_kit.dart';

/// Domain vocabulary → colour role. Authority, status, role, and identity kind
/// are the things the console colour-codes; everything else stays neutral.
CumulusTone toneForAuthority(String level) => switch (level) {
  'policy' => CumulusTone.danger,
  'standard' => CumulusTone.warn,
  'guideline' => CumulusTone.accent,
  'how-to' => CumulusTone.teal,
  _ => CumulusTone.neutral,
};

CumulusTone toneForStatus(String status) => switch (status) {
  'published' || 'active' => CumulusTone.success,
  'draft' => CumulusTone.warn,
  'expired' => CumulusTone.warn,
  'deprecated' || 'revoked' => CumulusTone.danger,
  'unattributed' => CumulusTone.danger,
  _ => CumulusTone.neutral,
};

CumulusTone toneForRole(String role) => switch (role) {
  'owner' => CumulusTone.violet,
  'admin' => CumulusTone.accent,
  _ => CumulusTone.neutral,
};

CumulusTone toneForKind(String kind) => switch (kind) {
  'agent' => CumulusTone.teal,
  'service' => CumulusTone.violet,
  _ => CumulusTone.neutral,
};

IconData iconForKind(String kind) => switch (kind) {
  'agent' => Icons.auto_awesome,
  'service' => Icons.dns_outlined,
  _ => Icons.person_outline,
};

String labelForKind(String kind) => switch (kind) {
  'agent' => 'Agent',
  'service' => 'Service',
  _ => 'Person',
};
