package com.example.kafka.filetransfer;

import com.example.kafka.filetransfer.cli.ConsumerCommand;
import com.example.kafka.filetransfer.cli.ProducerCommand;
import picocli.CommandLine;
import picocli.CommandLine.Command;

// --- MODIFICATIONS CI-DESSOUS ---
@Command(
    name = "file-transfer-app",
    mixinStandardHelpOptions = true,
    // NOUVEAU: Version dynamique lue depuis le MANIFEST.MF du JAR
    versionProvider = App.ManifestVersionProvider.class,
    description = {
        "Application principale pour le transfert de fichiers via Kafka.",
        "Utilisez l'une des commandes ci-dessous (producer, consumer) pour effectuer une action."
    },
    header = "====== Kafka File Transfer Suite ======",
    // NOUVEAU: Titre personnalisé pour la liste des commandes
    commandListHeading = "%nCommandes disponibles :%n",
    // NOUVEAU: Pied de page pour guider l'utilisateur
    footer = "%nUtilisez \"<commande> --help\" pour plus d'informations sur une commande spécifique.",
    subcommands = {
        ProducerCommand.class,
        ConsumerCommand.class,
        CommandLine.HelpCommand.class
    }
)
public class App {

    public static void main(String[] args) {
        int exitCode = new CommandLine(new App()).execute(args);
        System.exit(exitCode);
    }

    /**
     * NOUVELLE CLASSE INTERNE
     * VersionProvider qui lit la version de l'application depuis le fichier MANIFEST.MF
     * généré par Maven dans le JAR.
     */
    static class ManifestVersionProvider implements CommandLine.IVersionProvider {
        public String[] getVersion() throws Exception {
            Package pkg = App.class.getPackage();
            if (pkg == null) {
                return new String[] { "Version inconnue (non packagée)" };
            }
            String version = pkg.getImplementationVersion();
            return new String[] { version == null ? "Version inconnue" : version };
        }
    }
}
