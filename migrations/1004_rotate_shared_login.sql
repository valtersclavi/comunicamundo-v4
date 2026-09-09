UPDATE hefesto_product.app_credential
SET username = 'hefesto',
    password_salt = decode('1f80aeff31ba8239ebcc32588778660d', 'hex'),
    password_hash = decode('c119d70356fc85f2c2b9bb3d4c73bbbceeef751bc35e44fed6f240f0866dcc79', 'hex'),
    failed_attempts = 0,
    locked_until = NULL,
    updated_at = now()
WHERE singleton = true;
