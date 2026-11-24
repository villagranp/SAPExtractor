import { describe, expect, it } from 'vitest';
import { secretSchemaFor, toPayload, type JsonObjectSchema } from './schemaValues';

const snowflakeSecret: JsonObjectSchema = {
  properties: {
    private_key_pem: { type: 'string', format: 'password' },
    private_key_passphrase: { type: 'string', format: 'password' },
    token: { type: 'string', format: 'password' },
  },
  required: [],
};

describe('secretSchemaFor', () => {
  it('con PAT solo pide el token y no envía la clave escrita antes', () => {
    const schema = secretSchemaFor('snowflake', snowflakeSecret, { authentication: 'pat' });
    expect(Object.keys(schema.properties ?? {})).toEqual(['token']);
    expect(toPayload(schema, { private_key_pem: 'clave', token: 'pat-1' })).toEqual({
      token: 'pat-1',
    });
  });

  it('con par de claves (o destinos antiguos sin el campo) oculta el token', () => {
    for (const config of [{ authentication: 'key_pair' }, {}]) {
      const schema = secretSchemaFor('snowflake', snowflakeSecret, config);
      expect(Object.keys(schema.properties ?? {})).toEqual([
        'private_key_pem',
        'private_key_passphrase',
      ]);
    }
  });

  it('no toca otros tipos de destino', () => {
    const sqlserver: JsonObjectSchema = { properties: { password: { type: 'string' } } };
    expect(secretSchemaFor('sqlserver', sqlserver, { authentication: 'sql' })).toBe(sqlserver);
  });
});
