import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/setup';
import { sourceHandlers } from '../test/fixtures';
import { SourcesPage } from './SourcesPage';

describe('SourcesPage', () => {
  it('lista los orígenes con su sistema, tipo y delta', async () => {
    server.use(...sourceHandlers);
    renderWithProviders(<SourcesPage />);
    expect(await screen.findByText('FI_GL_14')).toBeInTheDocument();
    expect(screen.getByText('Partidas de mayor')).toBeInTheDocument();
    expect(screen.getAllByText('Extractor ODP').length).toBeGreaterThan(0);
    expect(screen.getByText('Delta de la ODQ')).toBeInTheDocument();
    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nuevo origen' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Cómo publicarlos en SAP' })).toBeInTheDocument();
  });
});
