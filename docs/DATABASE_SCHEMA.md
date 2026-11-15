# Database schema

Migration 0001 creates `dataset_versions`. Its primary key is a SHA-256 digest of series identity and canonical content. Each row records coverage, row count, price scale, acquisition time, file path and SHA-256, schema version, and validation status. A composite index supports filtering by provider, feed, instrument and timeframe.

Connections use foreign keys, WAL, synchronous FULL and a 5,000 ms busy timeout. Future decision versions and events require database-enforced immutability and foreign keys. Add normalized tables through migrations as their workflows are implemented.
