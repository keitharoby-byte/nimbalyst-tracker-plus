/** Live-host smoke checks; run with Nimbalyst's extension_test_run tool. */
import { expect, test } from '@nimbalyst/extension-sdk/testing';

test.describe('Tracker+', () => {
  test('extension is registered in the running host', async ({ page }) => {
    const extension = page.locator(
      '[data-extension-id="com.prediclear.nimbalyst-native-tracker-comments"]',
    );
    await expect(extension).toBeAttached({ timeout: 5_000 });
  });
});
