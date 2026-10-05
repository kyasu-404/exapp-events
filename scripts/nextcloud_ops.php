<?php
/** Deployment diagnostics for this ExApp only. Never exports secrets or sends mail. */
declare(strict_types=1);

require_once '/var/www/html/lib/base.php';

use OCA\AppAPI\PublicFunctions;
use OCA\AppAPI\Service\DaemonConfigService;
use OCP\IGroupManager;
use OCP\IUserManager;
use OCA\AppAPI\Service\UI\TopMenuService;
use OCP\Server;

try {
    $mode = $argv[1] ?? 'inspect';
    $group = Server::get(IGroupManager::class)->get('admin');
    $admins = $group ? array_values(array_filter($group->getUsers(), fn($user) => $user->isEnabled())) : [];
    if ($mode === 'inspect') {
        $daemon = Server::get(DaemonConfigService::class)->getDaemonConfigByName('harp_proxy_docker');
        $config = $daemon->getDeployConfig();
        echo json_encode([
            'admins' => array_map(fn($user) => $user->getUID(), $admins),
            'registries' => $config['registries'] ?? [],
            'net' => $config['net'] ?? null,
        ], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE) . "\n";
        exit(0);
    }
    if (!$admins) {
        throw new RuntimeException('No enabled admin');
    }
    $api = Server::get(PublicFunctions::class);
    $user = $admins[0]->getUID();
    if ($mode === 'security') {
        $entries = array_filter(Server::get(TopMenuService::class)->getExAppMenuEntries(), fn($entry) => $entry->getAppid() === 'exapp_events');
        foreach ($entries as $entry) {
            echo json_encode(['menu' => $entry->getName(), 'admin_required' => $entry->getAdminRequired()]) . "\n";
        }
        foreach (Server::get(IUserManager::class)->search('', 50) as $candidate) {
            if ($candidate->isEnabled() && !Server::get(IGroupManager::class)->isAdmin($candidate->getUID())) {
                $response = $api->exAppRequest('exapp_events', '/api/status', $candidate->getUID(), 'GET');
                echo json_encode(['normal_user_http' => $response->getStatusCode()]) . "\n";
                break;
            }
        }
        exit(0);
    }
    $route = $mode === 'settings' ? '/api/settings' : '/api/status';
    $method = 'GET';
    $params = [];
    if ($mode === 'configure-smtp') {
        $current = $api->exAppRequest('exapp_events', '/api/settings', $user, 'GET');
        $settings = json_decode((string)$current->getBody(), true);
        unset($settings['smtp_password_set']);
        $settings['smtp_mode'] = 'nextcloud';
        $settings['smtp_enabled'] = true;
        $route = '/api/settings';
        $method = 'PUT';
        $params = ['settings' => $settings];
    } elseif ($mode === 'diagnostics') {
        $route = '/api/diagnostics';
        $method = 'POST';
    } elseif ($mode === 'assets') {
        foreach (['/js/events-main.js', '/css/events-main.css'] as $asset) {
            $response = $api->exAppRequest('exapp_events', $asset, $user, 'GET');
            echo json_encode(['asset' => $asset, 'http' => $response->getStatusCode(), 'bytes' => strlen((string)$response->getBody())]) . "\n";
        }
        exit(0);
    } elseif (!in_array($mode, ['status', 'settings'], true)) {
        throw new RuntimeException('Unknown mode');
    }
    $response = $api->exAppRequest('exapp_events', $route, $user, $method, $params, ['timeout' => 60]);
    echo json_encode(['http' => $response->getStatusCode(), 'data' => json_decode((string)$response->getBody(), true)], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE) . "\n";
} catch (Throwable $e) {
    fwrite(STDERR, "ExApp operation failed; inspect its status/logs.\n");
    exit(1);
}
