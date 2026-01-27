"""
CLI tool for encrypting secrets in configuration files

Usage:
    # Generate encryption key
    python -m llamaindex_crew.config.encrypt_tool --generate-key
    
    # Encrypt a value
    python -m llamaindex_crew.config.encrypt_tool --encrypt "your_secret" --key "your_encryption_key"
    
    # Decrypt a value
    python -m llamaindex_crew.config.encrypt_tool --decrypt "encrypted_value" --key "your_encryption_key"
"""
import argparse
import sys
from .secure_config import ConfigLoader


def main():
    parser = argparse.ArgumentParser(
        description="Encrypt/decrypt secrets for AI Software Development Crew configuration"
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--generate-key",
        action="store_true",
        help="Generate a new encryption key"
    )
    group.add_argument(
        "--encrypt",
        metavar="VALUE",
        help="Encrypt a value"
    )
    group.add_argument(
        "--decrypt",
        metavar="ENCRYPTED_VALUE",
        help="Decrypt a value"
    )
    
    parser.add_argument(
        "--key",
        metavar="ENCRYPTION_KEY",
        help="Encryption key (required for encrypt/decrypt)"
    )
    
    args = parser.parse_args()
    
    if args.generate_key:
        key = ConfigLoader.generate_encryption_key()
        print("\n" + "=" * 70)
        print("🔑 Generated Encryption Key")
        print("=" * 70)
        print(f"\n{key}\n")
        print("⚠️  IMPORTANT: Store this key securely!")
        print("   - Never commit it to version control")
        print("   - Store in environment variable: CONFIG_ENCRYPTION_KEY")
        print("   - Or provide via --encryption-key argument")
        print("=" * 70 + "\n")
        
    elif args.encrypt:
        if not args.key:
            print("Error: --key is required for encryption")
            sys.exit(1)
        
        try:
            encrypted = ConfigLoader.encrypt_value(args.encrypt, args.key)
            print("\n" + "=" * 70)
            print("🔒 Encrypted Value")
            print("=" * 70)
            print(f"\nOriginal: {args.encrypt[:10]}... (hidden)")
            print(f"Encrypted: {encrypted}\n")
            print("Use in config.yaml as:")
            print(f"  api_key_encrypted: \"{encrypted}\"")
            print("=" * 70 + "\n")
        except Exception as e:
            print(f"Error encrypting value: {e}")
            sys.exit(1)
    
    elif args.decrypt:
        if not args.key:
            print("Error: --key is required for decryption")
            sys.exit(1)
        
        try:
            decrypted = ConfigLoader.decrypt_value(args.decrypt, args.key)
            print("\n" + "=" * 70)
            print("🔓 Decrypted Value")
            print("=" * 70)
            print(f"\nEncrypted: {args.decrypt[:20]}...")
            print(f"Decrypted: {decrypted}")
            print("=" * 70 + "\n")
        except Exception as e:
            print(f"Error decrypting value: {e}")
            print("Make sure you're using the correct encryption key")
            sys.exit(1)


if __name__ == "__main__":
    main()
