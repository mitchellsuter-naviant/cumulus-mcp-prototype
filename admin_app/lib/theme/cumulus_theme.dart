import 'package:flutter/material.dart';

/// Design tokens for the Cumulus console.
///
/// The console reads as a light, paper-white workspace: a soft cool canvas,
/// white cards with a hairline border and a very diffuse shadow, near-black
/// headings, and one indigo accent reserved for the active state and for
/// governed/agentic affordances.
abstract final class CumulusColors {
  static const canvas = Color(0xFFF6F7F9);
  static const surface = Color(0xFFFFFFFF);
  static const surfaceMuted = Color(0xFFF3F4F7);
  static const border = Color(0xFFEAEBF0);
  static const borderStrong = Color(0xFFDCDEE6);

  static const ink = Color(0xFF14161A);
  static const inkSecondary = Color(0xFF5A616E);
  static const inkMuted = Color(0xFF8B909C);

  static const accent = Color(0xFF4F46E5);
  static const accentSoft = Color(0xFFEEEEFC);
  static const accentInk = Color(0xFF4338CA);

  static const success = Color(0xFF0E9F6E);
  static const successSoft = Color(0xFFE7F6F0);
  static const warn = Color(0xFFC2790B);
  static const warnSoft = Color(0xFFFCF3E3);
  static const danger = Color(0xFFDC2626);
  static const dangerSoft = Color(0xFFFCECEC);
  static const teal = Color(0xFF0E9AA7);
  static const tealSoft = Color(0xFFE6F5F6);
}

/// Corner radii. Cards are generously rounded; controls sit one step tighter;
/// anything pill-shaped uses [pill].
abstract final class CumulusRadius {
  static const card = 16.0;
  static const control = 12.0;
  static const rail = 20.0;
  static const pill = 999.0;
}

abstract final class CumulusShadows {
  /// The only elevation in the system — wide, faint, barely there.
  static const card = <BoxShadow>[
    BoxShadow(
      color: Color(0x0D101828),
      blurRadius: 24,
      offset: Offset(0, 6),
      spreadRadius: -4,
    ),
  ];

  static const floating = <BoxShadow>[
    BoxShadow(
      color: Color(0x14101828),
      blurRadius: 28,
      offset: Offset(0, 10),
      spreadRadius: -6,
    ),
  ];
}

/// Small-caps, letterspaced labels do a lot of work in this design: section
/// headers, stat captions, and the eyebrow above every page title.
abstract final class CumulusText {
  static const eyebrow = TextStyle(
    fontSize: 11,
    fontWeight: FontWeight.w600,
    letterSpacing: 1.3,
    color: CumulusColors.inkMuted,
    height: 1.3,
  );

  static const pageTitle = TextStyle(
    fontSize: 26,
    fontWeight: FontWeight.w600,
    letterSpacing: -0.5,
    color: CumulusColors.ink,
    height: 1.2,
  );

  static const pageSubtitle = TextStyle(
    fontSize: 13.5,
    color: CumulusColors.inkSecondary,
    height: 1.45,
  );

  static const sectionTitle = TextStyle(
    fontSize: 11,
    fontWeight: FontWeight.w700,
    letterSpacing: 1.2,
    color: CumulusColors.ink,
  );

  static const sectionSubtitle = TextStyle(
    fontSize: 11.5,
    color: CumulusColors.inkMuted,
  );

  static const statLabel = TextStyle(
    fontSize: 10.5,
    fontWeight: FontWeight.w600,
    letterSpacing: 1.1,
    color: CumulusColors.inkMuted,
  );

  static const statValue = TextStyle(
    fontSize: 27,
    fontWeight: FontWeight.w600,
    letterSpacing: -0.6,
    color: CumulusColors.ink,
    height: 1.15,
  );

  static const statCaption = TextStyle(
    fontSize: 11.5,
    color: CumulusColors.inkMuted,
  );

  static const rowTitle = TextStyle(
    fontSize: 13.5,
    fontWeight: FontWeight.w600,
    color: CumulusColors.ink,
  );

  static const body = TextStyle(
    fontSize: 13,
    color: CumulusColors.inkSecondary,
    height: 1.45,
  );

  static const meta = TextStyle(
    fontSize: 12,
    color: CumulusColors.inkMuted,
    height: 1.4,
  );

  static const mono = TextStyle(
    fontFamily: 'JetBrains Mono',
    fontFamilyFallback: ['SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
    fontSize: 12.5,
    color: CumulusColors.ink,
    height: 1.5,
  );
}

/// Inter is loaded from `web/index.html`; the fallbacks keep the app legible
/// if that request never lands.
const _fontFamily = 'Inter';
const _fontFallback = <String>[
  '-apple-system',
  'BlinkMacSystemFont',
  'Segoe UI',
  'Roboto',
  'Helvetica Neue',
  'sans-serif',
];

ThemeData buildCumulusTheme() {
  const scheme = ColorScheme.light(
    primary: CumulusColors.accent,
    onPrimary: Colors.white,
    primaryContainer: CumulusColors.accentSoft,
    onPrimaryContainer: CumulusColors.accentInk,
    secondary: CumulusColors.teal,
    onSecondary: Colors.white,
    tertiary: CumulusColors.warn,
    error: CumulusColors.danger,
    onError: Colors.white,
    errorContainer: CumulusColors.dangerSoft,
    onErrorContainer: CumulusColors.danger,
    surface: CumulusColors.surface,
    onSurface: CumulusColors.ink,
    onSurfaceVariant: CumulusColors.inkSecondary,
    surfaceContainerHighest: CumulusColors.surfaceMuted,
    outline: CumulusColors.borderStrong,
    outlineVariant: CumulusColors.border,
  );

  InputBorder border(Color color, [double width = 1]) => OutlineInputBorder(
    borderRadius: BorderRadius.circular(CumulusRadius.control),
    borderSide: BorderSide(color: color, width: width),
  );

  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    fontFamily: _fontFamily,
    fontFamilyFallback: _fontFallback,
    scaffoldBackgroundColor: CumulusColors.canvas,
    canvasColor: CumulusColors.surface,
    dividerTheme: const DividerThemeData(
      color: CumulusColors.border,
      thickness: 1,
      space: 1,
    ),
    splashFactory: InkSparkle.splashFactory,
    textTheme: const TextTheme(
      titleLarge: CumulusText.pageTitle,
      titleMedium: CumulusText.rowTitle,
      titleSmall: CumulusText.sectionTitle,
      bodyMedium: CumulusText.body,
      bodySmall: CumulusText.meta,
      labelLarge: TextStyle(
        fontSize: 13,
        fontWeight: FontWeight.w600,
        color: CumulusColors.ink,
      ),
      labelSmall: CumulusText.statLabel,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: CumulusColors.surface,
      isDense: true,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
      hintStyle: const TextStyle(
        color: CumulusColors.inkMuted,
        fontSize: 13.5,
      ),
      labelStyle: const TextStyle(
        color: CumulusColors.inkSecondary,
        fontSize: 13.5,
      ),
      floatingLabelStyle: const TextStyle(
        color: CumulusColors.accent,
        fontSize: 13.5,
        fontWeight: FontWeight.w500,
      ),
      helperStyle: CumulusText.meta,
      enabledBorder: border(CumulusColors.border),
      border: border(CumulusColors.border),
      focusedBorder: border(CumulusColors.accent, 1.4),
      errorBorder: border(CumulusColors.danger),
      focusedErrorBorder: border(CumulusColors.danger, 1.4),
      disabledBorder: border(CumulusColors.border),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: CumulusColors.ink,
        foregroundColor: Colors.white,
        elevation: 0,
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
        textStyle: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(CumulusRadius.control),
        ),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: CumulusColors.ink,
        side: const BorderSide(color: CumulusColors.borderStrong),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
        textStyle: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(CumulusRadius.control),
        ),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: CumulusColors.inkSecondary,
        textStyle: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(CumulusRadius.control),
        ),
      ),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: IconButton.styleFrom(
        foregroundColor: CumulusColors.inkSecondary,
        highlightColor: CumulusColors.surfaceMuted,
      ),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: CumulusColors.surface,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(CumulusRadius.card),
        side: const BorderSide(color: CumulusColors.border),
      ),
      titleTextStyle: const TextStyle(
        fontFamily: _fontFamily,
        fontFamilyFallback: _fontFallback,
        fontSize: 16,
        fontWeight: FontWeight.w600,
        color: CumulusColors.ink,
      ),
      contentTextStyle: const TextStyle(
        fontFamily: _fontFamily,
        fontFamilyFallback: _fontFallback,
        fontSize: 13.5,
        color: CumulusColors.inkSecondary,
        height: 1.5,
      ),
    ),
    tooltipTheme: TooltipThemeData(
      decoration: BoxDecoration(
        color: const Color(0xFF23262E),
        borderRadius: BorderRadius.circular(8),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      textStyle: const TextStyle(
        fontFamily: _fontFamily,
        fontFamilyFallback: _fontFallback,
        fontSize: 12,
        color: Colors.white,
        fontWeight: FontWeight.w500,
      ),
      waitDuration: const Duration(milliseconds: 350),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: CumulusColors.ink,
      contentTextStyle: const TextStyle(
        fontFamily: _fontFamily,
        fontFamilyFallback: _fontFallback,
        fontSize: 13,
        color: Colors.white,
      ),
      behavior: SnackBarBehavior.floating,
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
    ),
    progressIndicatorTheme: const ProgressIndicatorThemeData(
      color: CumulusColors.accent,
      linearTrackColor: CumulusColors.surfaceMuted,
      strokeWidth: 2.5,
    ),
    scrollbarTheme: ScrollbarThemeData(
      thumbColor: WidgetStatePropertyAll(
        CumulusColors.inkMuted.withValues(alpha: 0.35),
      ),
      thickness: const WidgetStatePropertyAll(7),
      radius: const Radius.circular(999),
    ),
    dropdownMenuTheme: const DropdownMenuThemeData(
      menuStyle: MenuStyle(
        backgroundColor: WidgetStatePropertyAll(CumulusColors.surface),
        surfaceTintColor: WidgetStatePropertyAll(Colors.transparent),
      ),
    ),
    popupMenuTheme: PopupMenuThemeData(
      color: CumulusColors.surface,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        side: const BorderSide(color: CumulusColors.border),
      ),
    ),
  );
}
