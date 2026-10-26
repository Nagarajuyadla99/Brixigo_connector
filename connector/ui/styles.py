"""Brixigo Connector Operations Center visual system."""

APP_STYLESHEET = """
QWidget {
    font-family: "Segoe UI", "Noto Sans", sans-serif;
    font-size: 13px;
    color: #12202B;
}
QMainWindow, QDialog {
    background: #E8EDF2;
}
QWidget#AppShell {
    background: #E8EDF2;
}

/* —— Sidebar —— */
QFrame#Sidebar {
    background: #0B1C28;
    border: none;
}
QLabel#BrandMark {
    color: #F4F8FB;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    background: #2A8A9E;
    border-radius: 6px;
    padding: 6px 8px;
}
QLabel#BrandTitle {
    color: #F4F8FB;
    font-size: 14px;
    font-weight: 700;
}
QLabel#BrandSubtitle {
    color: #8FA3B3;
    font-size: 11px;
}
QFrame#SidebarFooter {
    background: #143044;
    border-radius: 8px;
}
QFrame#Skeleton {
    background: #E0E7EE;
    border-radius: 8px;
    border: none;
}
QLabel#NavSection {
    color: #6E8494;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
    padding: 16px 8px 6px 8px;
}
QPushButton#NavItem {
    background: transparent;
    color: #C5D4DE;
    border: none;
    border-radius: 6px;
    text-align: left;
    padding: 8px 12px 8px 10px;
    font-size: 13px;
    font-weight: 500;
    min-height: 32px;
}
QPushButton#NavItem:hover {
    background: #143044;
    color: #F4F8FB;
}
QPushButton#NavItem:checked {
    background: #1A3A4E;
    color: #FFFFFF;
    font-weight: 600;
    border-left: 2px solid #2A8A9E;
    padding-left: 12px;
}
QPushButton#NavItem:focus {
    outline: none;
    border: 1px solid #2A8A9E;
}

/* —— Top bar —— */
QFrame#TopBar {
    background: #FFFFFF;
    border-bottom: 1px solid #D3DCE4;
}
QLabel#PageTitle {
    font-size: 20px;
    font-weight: 700;
    color: #12202B;
    letter-spacing: -0.2px;
}
QLabel#PageSubtitle {
    font-size: 12px;
    color: #5C6B78;
    padding-right: 8px;
}
QLabel#LiveBadge {
    background: #E6F4EF;
    color: #0F6E56;
    border: 1px solid #B7E0D2;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.6px;
}
QLabel#LiveBadgeOff {
    background: #F4F7F9;
    color: #5C6B78;
    border: 1px solid #D3DCE4;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.6px;
}
QLabel#EnvBadge {
    background: #E8F0F8;
    color: #1D4E89;
    border: 1px solid #C5D8EE;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.6px;
}
QLabel#Muted {
    color: #5C6B78;
    font-size: 12px;
}
QLabel#Meta {
    color: #5C6B78;
    font-size: 11px;
}
QLabel#Avatar {
    background: #0E2433;
    color: #F4F8FB;
    border-radius: 14px;
    font-size: 11px;
    font-weight: 700;
    min-width: 28px;
    min-height: 28px;
}
QPushButton#IconButton {
    background: #F4F7F9;
    color: #12202B;
    border: 1px solid #D3DCE4;
    border-radius: 6px;
    padding: 6px 10px;
    font-weight: 600;
    font-size: 12px;
    min-width: 32px;
}
QPushButton#IconButton:hover { background: #E8EDF2; }
QPushButton#LiveToggle {
    background: #F4F7F9;
    color: #5C6B78;
    border: 1px solid #D3DCE4;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#LiveToggle:checked {
    background: #E6F4EF;
    color: #0F6E56;
    border: 1px solid #B7E0D2;
}
QPushButton#HeaderPrimary {
    background: #0E2433;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 600;
    font-size: 13px;
}

/* —— Surfaces —— */
QFrame#HeroPanel {
    background: #0E2433;
    border: none;
    border-radius: 10px;
}
QFrame#FlowStrip {
    background: #F4F7F9;
    border: 1px solid #D3DCE4;
    border-radius: 10px;
}
QFrame#MetricStrip {
    background: #FFFFFF;
    border: 1px solid #D3DCE4;
    border-radius: 10px;
}
QFrame#Surface {
    background: #FFFFFF;
    border: 1px solid #D3DCE4;
    border-radius: 10px;
}
QFrame#GhostSurface {
    background: transparent;
    border: none;
}
QFrame#WarningPanel {
    background: #FBF3E4;
    border: 1px solid #E8D3A4;
    border-radius: 10px;
}
QFrame#CriticalPanel {
    background: #F8EBE9;
    border: 1px solid #E8C4C0;
    border-radius: 10px;
}
QFrame#InfoPanel {
    background: #E8F0F8;
    border: 1px solid #C5D8EE;
    border-radius: 10px;
}
QFrame#DeviceRow, QFrame#AlertRow, QFrame#ActivityRow {
    background: transparent;
    border: none;
    border-bottom: 1px solid #E8EDF2;
}
QFrame#DeviceRow:hover, QFrame#AlertRow:hover, QFrame#ActivityRow:hover {
    background: #F4F7F9;
}
QFrame#MetricColumn {
    background: transparent;
    border: none;
}
QFrame#MetricColumn:hover {
    background: #F4F7F9;
    border-radius: 8px;
}
QFrame#FlowNode {
    background: #FFFFFF;
    border: 1px solid #D3DCE4;
    border-radius: 8px;
}
QFrame#PipelineStage {
    background: transparent;
    border: none;
}

QLabel#PanelTitle {
    font-size: 13px;
    font-weight: 700;
    color: #12202B;
    letter-spacing: 0.2px;
}
QLabel#SectionKicker {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.9px;
    color: #5C6B78;
}
QLabel#HeroKicker {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    color: #8FA3B3;
}
QLabel#HeroTitle {
    font-size: 22px;
    font-weight: 700;
    color: #F4F8FB;
    letter-spacing: -0.3px;
}
QLabel#HeroBody {
    font-size: 13px;
    color: #C5D4DE;
}
QLabel#HeroMeta {
    font-size: 11px;
    color: #8FA3B3;
}
QLabel#HeroAction {
    font-size: 12px;
    font-weight: 600;
    color: #F4F8FB;
}
QLabel#HealthValue {
    font-size: 28px;
    font-weight: 700;
    color: #F4F8FB;
}
QLabel#KpiValue {
    font-size: 22px;
    font-weight: 700;
    color: #12202B;
    letter-spacing: -0.3px;
}
QLabel#KpiLabel {
    font-size: 11px;
    color: #5C6B78;
    font-weight: 600;
    letter-spacing: 0.3px;
}
QLabel#KpiHint {
    font-size: 11px;
    color: #5C6B78;
}
QLabel#StatusText {
    font-size: 11px;
    font-weight: 600;
}
QLabel#RowTitle {
    font-size: 13px;
    font-weight: 600;
    color: #12202B;
}
QLabel#RowMeta {
    font-size: 11px;
    color: #5C6B78;
}
QLabel#EmptyState {
    color: #5C6B78;
    padding: 28px 16px;
    font-size: 13px;
}
QLabel#ErrorTitle {
    font-size: 14px;
    font-weight: 700;
    color: #B42318;
}

QPushButton {
    background: #0E2433;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    font-weight: 600;
    font-size: 13px;
}
QPushButton:hover { background: #173445; }
QPushButton:disabled { background: #9AA7B5; color: #E8EDF2; }
QPushButton:focus { outline: none; border: 1px solid #2A8A9E; }
QPushButton#Secondary {
    background: #EEF2F6;
    color: #12202B;
    border: 1px solid #D3DCE4;
}
QPushButton#Secondary:hover { background: #E0E7EE; }
QPushButton#Secondary:checked {
    background: #0E2433;
    color: #F4F8FB;
    border: 1px solid #0E2433;
}
QPushButton#RangeChip {
    background: #EEF2F6;
    color: #12202B;
    border: 1px solid #D3DCE4;
    min-width: 44px;
    max-width: 56px;
    padding: 5px 8px;
    font-size: 11px;
}
QPushButton#RangeChip:hover { background: #E0E7EE; }
QPushButton#RangeChip:checked {
    background: #0E2433;
    color: #F4F8FB;
    border: 1px solid #0E2433;
}
QPushButton#HeroSecondary {
    background: #1A3A4E;
    color: #F4F8FB;
    border: 1px solid #2A4A5E;
}
QPushButton#HeroSecondary:hover { background: #21485C; }
QPushButton#Danger {
    background: #B42318;
}
QPushButton#Danger:hover { background: #8F1C13; }
QPushButton#Ghost {
    background: transparent;
    color: #1D4E89;
    padding: 4px 8px;
    font-weight: 600;
}
QPushButton#Ghost:hover { background: #E8F0F8; }
QPushButton#Link {
    background: transparent;
    color: #1D4E89;
    padding: 0;
    font-size: 12px;
}

QLineEdit, QComboBox, QSpinBox, QDateEdit, QPlainTextEdit {
    background: white;
    border: 1px solid #C5D0DC;
    border-radius: 6px;
    padding: 6px 8px;
    min-height: 18px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus {
    border: 1px solid #2A8A9E;
}

QTableWidget {
    background: white;
    border: none;
    gridline-color: #E8EDF2;
    selection-background-color: #E8F0F8;
    outline: none;
    alternate-background-color: #F7FAFC;
}
QTableWidget::item {
    padding: 7px 10px;
    border: none;
}
QHeaderView::section {
    background: #F4F7F9;
    padding: 8px 10px;
    border: none;
    border-right: 1px solid #E8EDF2;
    border-bottom: 1px solid #D3DCE4;
    font-weight: 600;
    font-size: 11px;
    color: #5C6B78;
    letter-spacing: 0.3px;
}
QTabWidget::pane {
    border: 1px solid #D3DCE4;
    background: #FFFFFF;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    padding: 8px 16px;
    margin-right: 2px;
    color: #5C6B78;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected {
    color: #12202B;
    font-weight: 600;
    border-bottom: 2px solid #2A8A9E;
    background: transparent;
}
QTabBar::tab:hover { color: #12202B; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical {
    width: 10px;
    background: transparent;
    margin: 4px;
}
QScrollBar::handle:vertical {
    background: #B7C4D0;
    min-height: 32px;
    border-radius: 5px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip {
    background: #0E2433;
    color: #F4F8FB;
    border: none;
    padding: 6px 8px;
    font-size: 12px;
}
QPlainTextEdit#DiagnosticsPanel {
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    background: #0E2433;
    color: #D7E0EA;
    border-radius: 8px;
    border: none;
}
QMenu {
    background: #FFFFFF;
    border: 1px solid #D3DCE4;
    padding: 6px;
}
QMenu::item {
    padding: 6px 16px;
    border-radius: 4px;
}
QMenu::item:selected { background: #E8F0F8; }
QLabel#NotifyDot {
    background: #B42318;
    color: white;
    border-radius: 8px;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 5px;
}
"""
