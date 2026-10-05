<?php
declare(strict_types=1);

namespace OCA\ExAppEventsBridge\Controller;

use OCA\AppAPI\Attribute\AppAPIAuth;
use OCA\ExAppEventsBridge\AppInfo\Application;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\Attribute\PublicPage;
use OCP\AppFramework\Http\DataResponse;
use OCP\AppFramework\OCSController;
use OCP\IConfig;
use OCP\IRequest;
use OCP\Mail\IMailer;
use Psr\Log\LoggerInterface;
use Symfony\Component\Mailer\Transport\Smtp\EsmtpTransport;

class MailController extends OCSController {
    public function __construct(
        IRequest $request,
        private IMailer $mailer,
        private IConfig $config,
        private LoggerInterface $logger,
    ) {
        parent::__construct(Application::APP_ID, $request);
    }

    private function allowed(): bool {
        // AppAPIAuth middleware has already verified enabled state and the ExApp secret.
        return $this->request->getHeader('EX-APP-ID') === 'exapp_events';
    }

    #[AppAPIAuth]
    #[PublicPage]
    #[NoCSRFRequired]
    public function send(string $to = '', string $subject = '', string $text = '', string $html = ''): DataResponse {
        if (!$this->allowed()) {
            return new DataResponse(['error' => 'forbidden'], 403);
        }
        if (!filter_var($to, FILTER_VALIDATE_EMAIL) || preg_match('/[\r\n\x00]/', $to . $subject)
            || $subject === '' || strlen($subject) > 998 || $text === ''
            || strlen($text) > 262144 || strlen($html) > 524288) {
            return new DataResponse(['error' => 'invalid_message'], 422);
        }
        try {
            $message = $this->mailer->createMessage();
            $message->setTo([$to]);
            $message->setSubject($subject);
            $message->setPlainBody($text);
            if ($html !== '') {
                $message->setHtmlBody($html);
            }
            $message->setAutoSubmitted('auto-generated');
            // IMailer supplies Nextcloud's configured sender; credentials never leave Nextcloud.
            $failed = $this->mailer->send($message);
            if ($failed !== []) {
                return new DataResponse(['error' => 'recipient_rejected'], 502);
            }
            return new DataResponse(['status' => 'sent']);
        } catch (\Throwable $e) {
            $this->logger->warning('Events SMTP delivery failed', ['app' => Application::APP_ID]);
            return new DataResponse(['error' => 'mail_send_failed'], 502);
        }
    }

    #[AppAPIAuth]
    #[PublicPage]
    #[NoCSRFRequired]
    public function probe(): DataResponse {
        if (!$this->allowed()) {
            return new DataResponse(['error' => 'forbidden'], 403);
        }
        if ($this->config->getSystemValueString('mail_smtpmode', 'smtp') !== 'smtp') {
            return new DataResponse(['error' => 'smtp_not_configured'], 422);
        }
        try {
            // Same Symfony SMTP transport/configuration used by Nextcloud 34's IMailer.
            // Connect, negotiate TLS and authenticate, then QUIT. No message is sent.
            $transport = new EsmtpTransport(
                $this->config->getSystemValueString('mail_smtphost', '127.0.0.1'),
                $this->config->getSystemValueInt('mail_smtpport', 25),
                $this->config->getSystemValueString('mail_smtpsecure', '') === 'ssl' ? true : null,
            );
            $transport->getStream()->setTimeout(min(20, $this->config->getSystemValueInt('mail_smtptimeout', 10)));
            if ($this->config->getSystemValueBool('mail_smtpauth', false)) {
                $transport->setUsername($this->config->getSystemValueString('mail_smtpname', ''));
                $transport->setPassword($this->config->getSystemValueString('mail_smtppassword', ''));
            }
            $options = $this->config->getSystemValue('mail_smtpstreamoptions', []);
            if (is_array($options) && $options !== []) {
                $transport->getStream()->setStreamOptions(array_replace_recursive($transport->getStream()->getStreamOptions(), $options));
            }
            $host = parse_url($this->config->getSystemValueString('overwrite.cli.url', ''), PHP_URL_HOST);
            if (is_string($host) && $host !== '') {
                $transport->setLocalDomain($host);
            }
            $transport->start();
            $transport->stop();
            return new DataResponse(['status' => 'ok', 'transport' => 'nextcloud_smtp']);
        } catch (\Throwable $e) {
            $this->logger->warning('Events SMTP connection check failed', ['app' => Application::APP_ID]);
            return new DataResponse(['error' => 'smtp_probe_failed'], 502);
        }
    }
}
