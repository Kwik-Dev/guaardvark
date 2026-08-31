#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
DESKTOP_FILE="$SCRIPT_DIR/guaardvark.desktop"
INSTALL_DIR="$HOME/.local/share/applications"
INSTALLED_FILE="$INSTALL_DIR/guaardvark.desktop"

mkdir -p "$INSTALL_DIR"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Guaardvark
Comment=Guaardvark AI Assistant - Launch in app mode
Exec=$SCRIPT_DIR/start.sh --app-mode
Path=$SCRIPT_DIR
Icon=$SCRIPT_DIR/1_logo.png
Terminal=false
Categories=Utility;Application;Development;
StartupNotify=true
MimeType=
Keywords=AI;Assistant;LLM;Chat;
EOF

if ! cp "$DESKTOP_FILE" "$INSTALLED_FILE"; then
    echo "ERROR: failed to copy desktop file to $INSTALLED_FILE" >&2
    exit 1
fi

if ! chmod +x "$SCRIPT_DIR/start.sh"; then
    echo "ERROR: failed to chmod +x $SCRIPT_DIR/start.sh" >&2
    exit 1
fi

if command -v update-desktop-database &> /dev/null; then
    if update-desktop-database "$INSTALL_DIR"; then
        echo "Desktop database updated"
    else
        echo "WARNING: update-desktop-database failed; launcher copied but menu may not refresh until next login" >&2
    fi
fi

echo "Desktop launcher installed successfully!"
echo "You can now find 'Guaardvark' in your application menu."
echo ""
echo "To uninstall, run: rm $INSTALLED_FILE"
