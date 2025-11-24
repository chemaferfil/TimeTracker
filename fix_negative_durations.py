"""
Script para identificar y corregir registros con duraciones negativas.

El problema ocurre cuando check_out es anterior a check_in por unos pocos segundos,
típicamente cuando un admin cierra manualmente un fichaje usando formato HH:MM
(sin segundos), lo que resulta en check_out = HH:MM:00.

Si el check_in fue a las HH:MM:SS (donde SS > 0), entonces check_out < check_in.
"""

import os
import sys
from datetime import timedelta

# Asegurar que podemos importar desde el directorio actual
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.database import db
from models.models import TimeRecord
from main import app

def find_negative_durations():
    """Encuentra todos los registros con duraciones negativas."""
    with app.app_context():
        records = TimeRecord.query.filter(
            TimeRecord.check_in.isnot(None),
            TimeRecord.check_out.isnot(None)
        ).all()

        negative_records = []
        for record in records:
            duration = record.check_out - record.check_in
            if duration.total_seconds() < 0:
                negative_records.append({
                    'id': record.id,
                    'user_id': record.user_id,
                    'username': record.user.username if record.user else 'N/A',
                    'date': record.date,
                    'check_in': record.check_in,
                    'check_out': record.check_out,
                    'duration_seconds': duration.total_seconds()
                })

        return negative_records

def fix_negative_durations(dry_run=True):
    """
    Corrige los registros con duraciones negativas ajustando check_in a HH:MM:00.

    Args:
        dry_run: Si es True, solo muestra lo que se haría sin hacer cambios.
    """
    with app.app_context():
        negative_records = find_negative_durations()

        if not negative_records:
            print("✓ No se encontraron registros con duraciones negativas.")
            return

        print(f"\n{'='*80}")
        print(f"Encontrados {len(negative_records)} registros con duraciones negativas:")
        print(f"{'='*80}\n")

        for rec in negative_records:
            print(f"ID: {rec['id']}")
            print(f"  Usuario: {rec['username']} (ID: {rec['user_id']})")
            print(f"  Fecha: {rec['date']}")
            print(f"  Entrada: {rec['check_in'].strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Salida:  {rec['check_out'].strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Duración: {rec['duration_seconds']} segundos")

            # Proponer corrección: redondear check_in hacia abajo al minuto más cercano
            proposed_check_in = rec['check_in'].replace(second=0, microsecond=0)
            new_duration = (rec['check_out'] - proposed_check_in).total_seconds()
            print(f"  Corrección propuesta: check_in = {proposed_check_in.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Nueva duración: {new_duration} segundos ({new_duration/60:.1f} minutos)")
            print()

        if dry_run:
            print("\n" + "="*80)
            print("MODO DRY-RUN: No se han realizado cambios.")
            print("Para aplicar las correcciones, ejecuta: python fix_negative_durations.py --fix")
            print("="*80)
        else:
            print("\n" + "="*80)
            confirm = input("¿Deseas aplicar estas correcciones? (escribe 'SI' para confirmar): ")
            if confirm == "SI":
                for rec in negative_records:
                    record = TimeRecord.query.get(rec['id'])
                    if record:
                        record.check_in = record.check_in.replace(second=0, microsecond=0)

                db.session.commit()
                print(f"\n✓ Se han corregido {len(negative_records)} registros.")
            else:
                print("\nCancelado. No se realizaron cambios.")
            print("="*80)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Encuentra y corrige registros con duraciones negativas')
    parser.add_argument('--fix', action='store_true', help='Aplica las correcciones (por defecto solo muestra)')
    args = parser.parse_args()

    fix_negative_durations(dry_run=not args.fix)
