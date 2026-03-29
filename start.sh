#!/bin/bash
cd "$(dirname "$0")"
echo "=================================="
echo "  Bibliothèque EPUB"
echo "=================================="
echo ""
echo "Démarrage du serveur..."
echo ""

# Get local IPs
IPS=$(ip addr show | grep "inet " | grep -v "127.0.0.1" | awk '{print $2}' | cut -d/ -f1)
echo "Accès depuis l'iPad (réseau local) :"
for ip in $IPS; do
  echo "  → http://$ip:5000"
done
echo ""
echo "Appuyez sur Ctrl+C pour arrêter."
echo ""

python3 app.py
